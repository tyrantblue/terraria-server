"""进程级单例装配。

router 只通过这里拿 service，因此测试可以整包替换（见 tests/conftest.py 里对
`get_runtime` 的 dependency_overrides）。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.core.settings import Settings
from app.services.banlist import BanList
from app.services.console.audit import AuditLog
from app.services.guard_client import GuardClient
from app.services.log_events import LogEventWatcher
from app.services.metrics import MetricsSampler
from app.services.notifications import Notifier
from app.services.config_service import ConfigService
from app.services.console.channel import ConsoleChannel
from app.services.console.log_reader import LogReader
from app.services.operations import OperationRegistry
from app.services.scheduler import DAILY, INTERVAL, JobSpec, Scheduler
from app.services.server_service import ServerService
from app.services.status import StatusCollector
from app.services.world_service import WorldService


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    reader: LogReader
    channel: ConsoleChannel
    config: ConfigService
    status: StatusCollector
    server: ServerService
    world: WorldService
    operations: OperationRegistry
    banlist: BanList
    audit: AuditLog
    notifier: Notifier
    events: LogEventWatcher
    scheduler: Scheduler
    guard: GuardClient
    metrics: MetricsSampler


def build_job_specs(
    settings: Settings,
    server: ServerService,
    world: WorldService,
    notifier: Notifier,
) -> list[JobSpec]:
    """按配置组装定时任务（见 docs/roadmap.md 第一优先级）。"""
    specs: list[JobSpec] = []

    if settings.schedule_save_minutes > 0:
        specs.append(
            JobSpec(
                name="save",
                kind=INTERVAL,
                interval_seconds=settings.schedule_save_minutes * 60,
                description=f"每 {settings.schedule_save_minutes} 分钟保存一次世界",
                runner=lambda: server.scheduled_save(settings.schedule_save_skip_empty),
            )
        )

    if settings.schedule_backup_hours > 0:
        def run_backup() -> str:
            result = world.backup_now()
            pruned = world.prune_backups(settings.schedule_backup_keep)
            return (
                f"backup={result['backup']} files={len(result['files'])} "
                f"pruned={len(pruned['removed'])}"
            )

        specs.append(
            JobSpec(
                name="backup",
                kind=INTERVAL,
                interval_seconds=settings.schedule_backup_hours * 3600,
                description=(
                    f"每 {settings.schedule_backup_hours} 小时备份一次，"
                    f"保留最近 {settings.schedule_backup_keep} 份"
                ),
                runner=run_backup,
            )
        )

    if settings.schedule_console_check_seconds > 0:
        specs.append(
            JobSpec(
                name="console",
                kind=INTERVAL,
                interval_seconds=settings.schedule_console_check_seconds,
                description=(
                    f"每 {settings.schedule_console_check_seconds} 秒发一条命令，"
                    "确认控制台/日志管道还活着"
                ),
                runner=server.console_heartbeat,
            )
        )

    if settings.schedule_restart_at:
        specs.append(
            JobSpec(
                name="restart",
                kind=DAILY,
                at=settings.schedule_restart_at,
                description=(
                    f"每天 {settings.schedule_restart_at}（{settings.schedule_timezone}）重启"
                    + ("，有人在线则跳过" if settings.schedule_restart_skip_if_players else "")
                ),
                runner=lambda: server.scheduled_restart(
                    skip_if_players=settings.schedule_restart_skip_if_players,
                    warn_minutes=settings.schedule_restart_warn_minutes,
                ),
            )
        )
    return specs


def _job_result_notifier(notifier: Notifier):
    def on_result(name: str, status: str, detail: str | None) -> None:
        if status == "failed":
            notifier.notify(
                "schedule_failed", f"定时任务 {name} 执行失败", level="error",
                detail={"detail": detail or ""},
            )
        elif name == "backup" and status == "succeeded":
            notifier.notify(
                "backup_done", "自动备份完成", level="success", detail={"detail": detail or ""}
            )
        elif (
            name == "console"
            and status == "succeeded"
            and (detail or "").startswith("stalled:")
        ):
            notifier.notify(
                "log_stalled", "面板读不到服务端状态：日志管道可能停更",
                level="error", detail={"detail": detail or ""},
            )
        elif name == "restart" and status == "skipped":
            notifier.notify(
                "restart_skipped", "定时重启已跳过", level="warning",
                detail={"detail": detail or ""},
            )
    return on_result


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or Settings()
    reader = LogReader(settings.log_file)
    channel = ConsoleChannel(
        fifo=settings.fifo,
        reader=reader,
        lock_path=settings.console_lock,
        sentinel=settings.console_sentinel,
        timeout=settings.console_timeout,
        lock_timeout=settings.console_lock_timeout,
    )
    config = ConfigService(settings.config_file)
    status = StatusCollector(
        channel,
        reader,
        ttl=settings.status_ttl,
        static_ttl=settings.static_ttl,
    )
    operations = OperationRegistry()
    notifier = Notifier(
        settings.notify_webhook_url,
        fmt=settings.notify_format,
        events=settings.notify_events,
    )
    server_service = ServerService(
        channel,
        status,
        config,
        operations,
        reader,
        stall_cooldown=settings.console_stall_cooldown,
        log_stall_seconds=settings.log_stall_seconds,
    )
    world_service = WorldService(
        settings.worlds_dir,
        config,
        channel,
        status,
        reader,
        operations,
        settings.backup_dir,
        max_upload_bytes=settings.world_upload_max_bytes,
    )
    scheduler = Scheduler(
        build_job_specs(settings, server_service, world_service, notifier),
        timezone=settings.schedule_timezone,
        on_result=_job_result_notifier(notifier),
    )
    metrics = MetricsSampler(
        settings.worlds_dir,
        players_provider=lambda: len(status.player_entries()),
        interval=settings.metrics_interval_seconds,
        retention=settings.metrics_retention_points,
    )
    return Runtime(
        settings=settings,
        reader=reader,
        channel=channel,
        config=config,
        status=status,
        server=server_service,
        world=world_service,
        operations=operations,
        banlist=BanList(settings.worlds_dir, settings.config_file),
        audit=AuditLog(settings.control_dir / "audit.log"),
        notifier=notifier,
        events=LogEventWatcher(reader, notifier),
        scheduler=scheduler,
        guard=GuardClient(
            settings.control_dir / "guard-state.json",
            settings.control_dir / "guard-commands.jsonl",
        ),
        metrics=metrics,
    )


@lru_cache(maxsize=1)
def get_runtime() -> Runtime:
    return build_runtime()


def reset_runtime() -> None:
    get_runtime.cache_clear()
