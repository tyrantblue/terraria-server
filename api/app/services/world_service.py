"""世界存档用例：列出、上传、切换。

旧实现把这些逻辑写在 routers/world.py 里（含 time.sleep 与 30 秒轮询），
只能在 HTTP 请求里复用。现在下沉到 service，行为与旧版保持一致。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncIterator, Protocol

from app.core.errors import BadRequest, Conflict, NotFound, UpstreamFailed
from app.services.config_service import ConfigService
from app.services.console.channel import ConsoleChannel
from app.services.console.log_reader import LogReader
from app.services.status import StatusCollector

SWITCH_TIMEOUT = 30.0
SAVE_GRACE = 1.0
CHUNK_SIZE = 1024 * 1024


class AsyncReader(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


class WorldService:
    def __init__(
        self,
        worlds_dir: Path,
        config: ConfigService,
        channel: ConsoleChannel,
        status: StatusCollector,
        reader: LogReader,
    ) -> None:
        self.worlds_dir = worlds_dir
        self._config = config
        self._channel = channel
        self._status = status
        self._reader = reader

    # -- 查询 ---------------------------------------------------------
    def active_world_file(self) -> str | None:
        value = self._config.get("world")
        return Path(value).name if value else None

    def list(self) -> tuple[list[dict[str, object]], str | None]:
        active = self.active_world_file()
        worlds: list[dict[str, object]] = []
        for path in sorted(self.worlds_dir.glob("*.wld")):
            stat = path.stat()
            worlds.append(
                {
                    "name": path.stem,
                    "file": path.name,
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "active": path.name == active,
                }
            )
        return worlds, active

    # -- 上传 ---------------------------------------------------------
    async def upload(self, filename: str | None, stream: AsyncReader) -> tuple[str, str, int]:
        if not filename:
            raise BadRequest("filename is required")

        safe_name = Path(filename).name
        if not safe_name.lower().endswith(".wld"):
            raise BadRequest("only .wld files are allowed")

        destination = self.worlds_dir / safe_name
        if destination.exists():
            raise Conflict(f"world already exists: {safe_name}")

        with destination.open("wb") as handle:
            while True:
                chunk = await stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)

        return destination.stem, destination.name, destination.stat().st_size

    # -- 切换 ---------------------------------------------------------
    def switch(self, requested: str) -> str:
        filename = Path(requested).name
        if filename != requested:
            raise BadRequest("invalid filename")
        if not filename.lower().endswith(".wld"):
            raise BadRequest("only .wld files are allowed")

        world_file = self.worlds_dir / filename
        if not world_file.is_file():
            raise NotFound(f"world not found: {filename}")

        # 1. 先保存当前世界（单向命令：save 会阻塞主循环数秒，不等回显）
        try:
            self._channel.send("save")
        except Exception as exc:  # noqa: BLE001 - 转成与旧版一致的 500
            raise UpstreamFailed(f"failed to save world: {exc}") from exc

        # 给 Terraria 一点时间完成保存（与旧版一致）
        time.sleep(SAVE_GRACE)

        # 2. 修改 serverconfig.txt
        if not self._config.path.exists():
            raise UpstreamFailed("server config file not found")
        self._config.set("world", f"/worlds/{filename}")

        # 3. 退出 Terraria（单向；进程随后退出，哨兵永远不会回来）
        try:
            self._channel.send("exit")
        except Exception as exc:  # noqa: BLE001
            raise UpstreamFailed(f"failed to stop server: {exc}") from exc

        # 4. 等待服务端重新可用
        deadline = time.monotonic() + SWITCH_TIMEOUT
        while time.monotonic() < deadline:
            try:
                if "Terraria Server" in self._channel.run("version", timeout=2.0):
                    self._status.invalidate()
                    return filename
            except Exception:  # noqa: BLE001 - 重启期间 FIFO 会短暂不可用
                pass
            time.sleep(1)

        raise UpstreamFailed("Terraria server did not restart within 30 seconds", status_code=504)
