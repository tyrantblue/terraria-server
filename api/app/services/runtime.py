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
from app.services.config_service import ConfigService
from app.services.console.channel import ConsoleChannel
from app.services.console.log_reader import LogReader
from app.services.operations import OperationRegistry
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
    return Runtime(
        settings=settings,
        reader=reader,
        channel=channel,
        config=config,
        status=status,
        server=ServerService(channel, status, config, operations),
        world=WorldService(
            settings.worlds_dir,
            config,
            channel,
            status,
            reader,
            operations,
            settings.backup_dir,
        ),
        operations=operations,
        banlist=BanList(settings.worlds_dir, settings.config_file),
        audit=AuditLog(),
    )


@lru_cache(maxsize=1)
def get_runtime() -> Runtime:
    return build_runtime()


def reset_runtime() -> None:
    get_runtime.cache_clear()
