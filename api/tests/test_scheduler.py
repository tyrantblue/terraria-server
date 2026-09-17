"""定时任务：配置装配、到期计算、手动执行与跳过逻辑。"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.settings import Settings
from app.services.scheduler import DAILY, INTERVAL, JobSpec, Scheduler
from app.services.runtime import build_job_specs


def wait_for_command(fake, needle: str, timeout: float = 2.0) -> bool:
    """send() 是单向的，假服务端在另一个线程里收；这里等它出现。"""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(needle in item for item in fake.commands):
            return True
        time.sleep(0.02)
    return False


def make_scheduler(tmp_path, runners, *, at="05:00", tz="Asia/Shanghai"):
    specs = [
        JobSpec(name="save", kind=INTERVAL, interval_seconds=60, runner=runners["save"]),
        JobSpec(name="restart", kind=DAILY, at=at, runner=runners["restart"]),
    ]
    return Scheduler(specs, timezone=tz, tick=0.05)


def test_job_specs_follow_settings(rt) -> None:
    settings = Settings(
        schedule_save_minutes=10,
        schedule_save_skip_empty=True,
        schedule_backup_hours=3,
        schedule_backup_keep=5,
        schedule_restart_at="05:30",
        schedule_enabled=False,
    )
    specs = build_job_specs(settings, rt.server, rt.world, rt.notifier)
    names = {spec.name: spec for spec in specs}
    assert names["save"].kind == INTERVAL and names["save"].interval_seconds == 600
    assert names["backup"].interval_seconds == 3 * 3600
    assert names["restart"].kind == DAILY and names["restart"].at == "05:30"


def test_disabled_jobs_are_not_created(rt) -> None:
    settings = Settings(
        schedule_save_minutes=0,
        schedule_backup_hours=0,
        schedule_restart_at="",
        schedule_enabled=False,
    )
    assert build_job_specs(settings, rt.server, rt.world, rt.notifier) == []


def test_daily_next_run_uses_configured_timezone(tmp_path) -> None:
    scheduler = make_scheduler(tmp_path, {"save": lambda: "ok", "restart": lambda: "ok"})
    job = next(item for item in scheduler.list_jobs() if item["name"] == "restart")
    local = datetime.fromtimestamp(job["next_run"], scheduler.tz)
    assert (local.hour, local.minute) == (5, 0)
    # 容器默认是 UTC，时区没生效的话这里会差 8 小时
    assert scheduler.tz.key == "Asia/Shanghai"


def test_unknown_timezone_falls_back_to_utc(tmp_path) -> None:
    scheduler = Scheduler([], timezone="Mars/Olympus")
    assert scheduler.tz.key == "UTC"


def test_run_now_records_result(tmp_path) -> None:
    calls = []
    scheduler = make_scheduler(
        tmp_path,
        {"save": lambda: calls.append("save") or "saved", "restart": lambda: "submitted: x"},
    )
    entry = scheduler.run_now("save")
    assert entry["status"] == "succeeded"
    assert calls == ["save"]
    job = next(item for item in scheduler.list_jobs() if item["name"] == "save")
    assert job["run_count"] == 1 and job["last_status"] == "succeeded"


def test_skipped_and_failed_are_recorded(tmp_path) -> None:
    def boom() -> str:
        raise RuntimeError("disk full")

    scheduler = make_scheduler(
        tmp_path,
        {"save": lambda: "skipped: 无人在线", "restart": boom},
    )
    assert scheduler.run_now("save")["status"] == "skipped"
    assert scheduler.run_now("restart")["status"] == "failed"

    jobs = {item["name"]: item for item in scheduler.list_jobs()}
    assert jobs["save"]["skipped_count"] == 1
    assert jobs["restart"]["failed_count"] == 1
    assert "disk full" in jobs["restart"]["last_detail"]


def test_unknown_job_raises(tmp_path) -> None:
    scheduler = make_scheduler(tmp_path, {"save": lambda: "ok", "restart": lambda: "ok"})
    with pytest.raises(KeyError):
        scheduler.run_now("nope")


def test_on_result_hook_fires(tmp_path) -> None:
    seen: list[tuple[str, str]] = []
    specs = [JobSpec(name="backup", kind=INTERVAL, interval_seconds=60, runner=lambda: "done")]
    scheduler = Scheduler(specs, on_result=lambda n, s, d: seen.append((n, s)))
    scheduler.run_now("backup")
    # 手动执行不触发通知（避免手点一下也发消息）
    assert seen == []


# ---------------------------------------------------------------- 服务层跳过逻辑
def test_scheduled_save_skips_when_empty(rt) -> None:
    assert rt.server.scheduled_save(skip_if_empty=True) == "skipped: 无人在线"
    assert rt.server.scheduled_save(skip_if_empty=False) == "saved"


def test_scheduled_save_runs_when_players_online(rt, fake_terraria) -> None:
    fake_terraria.players = ("CTQ (121.33.239.89:44176)",)
    assert rt.server.scheduled_save(skip_if_empty=True) == "saved"


def test_scheduled_restart_skips_when_players_online(rt, fake_terraria) -> None:
    fake_terraria.players = ("CTQ (121.33.239.89:44176)",)
    detail = rt.server.scheduled_restart(skip_if_players=True, warn_minutes=5)
    assert detail.startswith("skipped")
    assert wait_for_command(fake_terraria, "say")


def test_scheduled_restart_submits_operation(rt) -> None:
    detail = rt.server.scheduled_restart(skip_if_players=True, warn_minutes=0)
    assert detail.startswith("submitted: ")
    assert rt.operations.get(detail.split(": ", 1)[1]).kind == "server.restart"


def test_scheduler_api(client) -> None:
    body = client.get("/api/v1/scheduler").json()
    assert body["enabled"] is False          # 测试环境禁用了后台线程
    assert body["timezone"] == "Asia/Shanghai"
    assert client.post("/api/v1/scheduler/__nope__/run").status_code == 404
