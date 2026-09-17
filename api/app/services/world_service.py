"""世界存档用例：列出、上传、切换。

旧实现把这些逻辑写在 routers/world.py 里（含 time.sleep 与 30 秒轮询），
只能在 HTTP 请求里复用。现在下沉到 service，行为与旧版保持一致。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import AsyncIterator, Protocol

import shutil
from datetime import datetime

from app.core.errors import BadRequest, Conflict, NotFound, UpstreamFailed
from app.services.config_service import ConfigService
from app.services.console.channel import ConsoleChannel
from app.services.console.log_reader import LogReader
from app.services.operations import FAILED, SUCCEEDED, Operation, OperationRegistry
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
        operations: OperationRegistry,
        backup_dir: Path,
    ) -> None:
        self.worlds_dir = worlds_dir
        self._config = config
        self._channel = channel
        self._status = status
        self._reader = reader
        self._operations = operations
        self.backup_dir = backup_dir

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

    def resolve(self, requested: str) -> str:
        """校验世界文件名，返回文件名。"""
        filename = Path(requested).name
        if filename != requested or not filename.lower().endswith(".wld"):
            raise BadRequest("invalid filename")
        if not (self.worlds_dir / filename).is_file():
            raise NotFound(f"world not found: {filename}")
        return filename

    def delete(self, requested: str) -> str:
        filename = self.resolve(requested)
        if filename == self.active_world_file():
            raise Conflict("不能删除当前激活的世界，请先切换到别的世界")
        (self.worlds_dir / filename).unlink()
        return filename

    # -- 切换（长任务） -------------------------------------------------
    def activate(self, requested: str) -> Operation:
        """切换世界：保存 → 改配置 → 重启。后台执行，返回操作句柄。"""
        filename = self.resolve(requested)
        return self._operations.submit(
            "world.activate", lambda progress: self._activate_job(filename, progress)
        )

    def _activate_job(self, filename: str, progress) -> dict[str, object]:
        progress(5, f"准备切换到 {filename}")
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
        progress(20, "写入 serverconfig.txt")
        self._config.set("world", f"/worlds/{filename}")

        # 3. 退出 Terraria（单向；进程随后退出，哨兵永远不会回来）
        progress(30, "关闭服务端")
        try:
            self._channel.send("exit")
        except Exception as exc:  # noqa: BLE001
            raise UpstreamFailed(f"failed to stop server: {exc}") from exc

        # 4. 等待服务端重新可用
        deadline = time.monotonic() + SWITCH_TIMEOUT
        step = 0
        while time.monotonic() < deadline:
            try:
                if "Terraria Server" in self._channel.run("version", timeout=2.0):
                    self._status.invalidate()
                    progress(95, "服务端已恢复")
                    return {"world": filename}
            except Exception:  # noqa: BLE001 - 重启期间 FIFO 会短暂不可用
                pass
            step += 1
            progress(min(90, 40 + step * 5), "等待服务端重新监听")
            time.sleep(1)

        raise UpstreamFailed("Terraria server did not restart within 30 seconds", status_code=504)

    def activate_and_wait(self, requested: str, timeout: float = 90.0) -> str:
        """旧接口 POST /api/world/switch 用：提交操作并同步等到结束。"""
        operation = self.activate(requested)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and operation.state not in (SUCCEEDED, FAILED):
            time.sleep(0.25)
        if operation.state == SUCCEEDED:
            return str((operation.result or {}).get("world", requested))
        if operation.state == FAILED:
            raise UpstreamFailed(operation.error or "world switch failed", status_code=504)
        raise UpstreamFailed("world switch did not finish in time", status_code=504)

    # -- 备份 ---------------------------------------------------------
    def list_backups(self) -> list[dict[str, object]]:
        if not self.backup_dir.is_dir():
            return []
        result: list[dict[str, object]] = []
        for entry in sorted(self.backup_dir.iterdir(), reverse=True):
            if not entry.is_dir():
                continue
            files = [p for p in entry.rglob("*") if p.is_file()]
            result.append(
                {
                    "name": entry.name,
                    "created_at": entry.stat().st_mtime,
                    "files": len(files),
                    "size": sum(p.stat().st_size for p in files),
                }
            )
        return result

    def backup(self, requested: str | None = None) -> Operation:
        """把世界文件（可指定单个）与 serverconfig.txt 复制到 backup/<时间戳>/。"""
        filename = self.resolve(requested) if requested else None
        return self._operations.submit(
            "world.backup", lambda progress: self._backup_job(filename, progress)
        )

    def _backup_job(self, filename: str | None, progress) -> dict[str, object]:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.backup_dir / stamp
        target.mkdir(parents=True, exist_ok=True)
        sources = (
            [self.worlds_dir / filename]
            if filename
            else sorted(self.worlds_dir.glob("*.wld"))
        )
        if not sources:
            raise NotFound("没有可备份的世界文件")

        copied: list[str] = []
        for index, source in enumerate(sources, start=1):
            shutil.copy2(source, target / source.name)
            copied.append(source.name)
            progress(int(index / (len(sources) + 1) * 100), f"复制 {source.name}")

        if self._config.path.exists():
            shutil.copy2(self._config.path, target / self._config.path.name)
            copied.append(self._config.path.name)

        progress(100, "备份完成")
        return {"backup": stamp, "files": copied}
