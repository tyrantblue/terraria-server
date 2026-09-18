"""世界存档用例：列出、上传、切换。

旧实现把这些逻辑写在 routers/world.py 里（含 time.sleep 与 30 秒轮询），
只能在 HTTP 请求里复用。现在下沉到 service，行为与旧版保持一致。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Protocol

import hashlib
import re
import shutil
from datetime import datetime
from uuid import uuid4

from app.core.errors import (
    BadRequest,
    Conflict,
    InsufficientStorage,
    NotFound,
    PayloadTooLarge,
    UpstreamFailed,
)
from app.services.config_service import ConfigService
from app.services.console.channel import ConsoleChannel
from app.services.console.log_reader import LogReader
from app.services.operations import Operation, OperationRegistry
from app.services.status import StatusCollector
from app.services.world_header import read_world_metadata

SWITCH_TIMEOUT = 30.0
#: 自动备份目录名（20260917-080632）才会被保留策略清理
BACKUP_NAME_RE = re.compile(r"^\d{8}-\d{6}$")
SAVE_GRACE = 1.0
CHUNK_SIZE = 1024 * 1024
#: 上传世界时的临时后缀：先写 .part，成功后再原子改名
PART_SUFFIX = ".part"
#: 写入世界文件前希望保留的最小余量（字节）
MIN_FREE_BYTES = 64 * 1024 * 1024
#: 余量要求：至少 2× 文件大小——Terraria 保存时还会自己写一份 .bak
REQUIRED_FREE_FACTOR = 2


def free_bytes(path: Path) -> int | None:
    """卷上的可用字节数（os.statvfs）；拿不到就返回 None（不阻塞上传）。"""
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return None


def required_free_bytes(expected: int) -> int:
    """写入 expected 字节前希望保留的余量。"""
    return max(MIN_FREE_BYTES, expected * REQUIRED_FREE_FACTOR)


def precheck_upload(worlds_dir: Path, *, declared_size: int, max_bytes: int) -> None:
    """上传前的两个预检：大小上限（413）与磁盘余量（507）。

    必须在**进路由之前**用 Content-Length 做：Starlette 会先把整个 multipart
    落到临时文件，等路由函数拿到 `UploadFile` 时字节已经写完了，那时再拒
    只能防住 worlds/，防不住临时目录被写满。service 层仍保留同样的检查，
    用于没有 Content-Length 的 chunked 上传。
    """
    if declared_size > max_bytes:
        raise PayloadTooLarge(
            f"世界文件超过上限 {_mb(max_bytes)} MB（本次 {_mb(declared_size)} MB）",
            details={"limit_bytes": max_bytes, "declared_bytes": declared_size},
        )
    free = free_bytes(worlds_dir)
    if free is None:
        return
    needed = required_free_bytes(declared_size)
    if free < needed:
        raise InsufficientStorage(
            f"磁盘余量不足：至少需要 {_mb(needed)} MB，当前可用 {_mb(free)} MB。"
            "服务端保存世界时还会再写一份 .bak，所以需要 2× 文件大小的余量。",
            details={"free_bytes": free, "required_bytes": needed},
        )


def _mb(value: int) -> int:
    return round(value / (1024 * 1024))


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
        max_upload_bytes: int = 500 * 1024 * 1024,
    ) -> None:
        self.worlds_dir = worlds_dir
        self._config = config
        self._channel = channel
        self._status = status
        self._reader = reader
        self._operations = operations
        self.backup_dir = backup_dir
        self.max_upload_bytes = max_upload_bytes

    # -- 查询 ---------------------------------------------------------
    def active_world_file(self) -> str | None:
        value = self._config.get("world")
        return Path(value).name if value else None

    def list(self) -> tuple[list[dict[str, object]], str | None]:
        active = self.active_world_file()
        worlds: list[dict[str, object]] = []
        for path in sorted(self.worlds_dir.glob("*.wld")):
            stat = path.stat()
            metadata = read_world_metadata(path)
            worlds.append(
                {
                    "name": path.stem,
                    "file": path.name,
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                    "active": path.name == active,
                    # 头部解析失败就是 null——面板显示"未知"，接口照常 200
                    "metadata": metadata.as_dict() if metadata else None,
                }
            )
        return worlds, active

    # -- 上传 ---------------------------------------------------------
    async def upload(
        self,
        filename: str | None,
        stream: AsyncReader,
        *,
        declared_size: int | None = None,
    ) -> tuple[str, str, int]:
        """流式接收一个 `.wld`。

        三道防线（issue #3）：
        1. `Content-Length` 预检（中间件在 spool 之前就做，这里再兜一次）；
        2. 写入时累计字节数，超过上限立刻 413；
        3. 每写一块都看一次磁盘余量（至少 2× 已写字节），不足 507。

        文件先写成 `xxx.wld.part`，全部成功后才 `os.replace` 成正式文件：
        任何中途失败都不会留下半个世界文件。
        """
        if not filename:
            raise BadRequest("filename is required")

        safe_name = Path(filename).name
        if not safe_name.lower().endswith(".wld"):
            raise BadRequest("only .wld files are allowed")

        if declared_size is not None:
            precheck_upload(
                self.worlds_dir,
                declared_size=declared_size,
                max_bytes=self.max_upload_bytes,
            )

        destination = self.worlds_dir / safe_name
        if destination.exists():
            raise Conflict(f"world already exists: {safe_name}")

        # 临时名必须唯一：两个人同时上传同名世界时，共用一个 .part 会让两边
        # 交错写同一个文件，先完成的 os.replace 之后另一个必然写坏/失败。
        temp = destination.with_name(f"{destination.name}.{uuid4().hex[:8]}{PART_SUFFIX}")
        written = 0
        try:
            if declared_size is None:
                self._require_space(0)
            with temp.open("wb") as handle:
                while True:
                    chunk = await stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > self.max_upload_bytes:
                        raise PayloadTooLarge(
                            f"世界文件超过上限 {_mb(self.max_upload_bytes)} MB",
                            details={
                                "limit_bytes": self.max_upload_bytes,
                                "received_bytes": written,
                            },
                        )
                    handle.write(chunk)
                    self._require_space(written)
            if not written:
                raise BadRequest("上传内容为空（不是有效的 .wld 文件）")
            os.replace(temp, destination)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

        return destination.stem, destination.name, destination.stat().st_size

    def _require_space(self, written: int) -> None:
        """写入过程中的磁盘余量检查：free >= max(64MB, 2×已写)。"""
        free = free_bytes(self.worlds_dir)
        if free is None:
            return
        needed = required_free_bytes(written)
        if free < needed:
            raise InsufficientStorage(
                f"磁盘余量不足：至少需要 {_mb(needed)} MB，当前可用 {_mb(free)} MB",
                details={"free_bytes": free, "required_bytes": needed, "written_bytes": written},
            )

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
        self._wait_until_up(progress, base=40, span=50)
        return {"world": filename}

    # -- 备份 ---------------------------------------------------------
    def list_backups(self, include_auto: bool = True) -> list[dict[str, object]]:
        """手工备份目录 + Terraria 自己写的 .wld.bak/.bak2（kind=auto）。"""
        result: list[dict[str, object]] = []
        if self.backup_dir.is_dir():
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
                        "kind": "manual" if BACKUP_NAME_RE.match(entry.name) else "legacy",
                        "restorable": any(p.suffix == ".wld" for p in files),
                        "path": str(entry),
                    }
                )
        if include_auto:
            for path in sorted(self.worlds_dir.glob("*.wld.bak*")):
                result.append(
                    {
                        "name": f"auto:{path.name}",
                        "created_at": path.stat().st_mtime,
                        "files": 1,
                        "size": path.stat().st_size,
                        "kind": "auto",
                        "restorable": True,
                        "path": str(path),
                    }
                )
        return result

    def prune_backups(self, keep: int) -> dict[str, object]:
        """只清理形如 20260917-080632 的自动/手工备份目录，保留 pre-restore 与迁移备份。"""
        if keep <= 0 or not self.backup_dir.is_dir():
            return {"removed": [], "kept": 0, "disabled": keep <= 0}
        candidates = sorted(
            (d for d in self.backup_dir.iterdir() if d.is_dir() and BACKUP_NAME_RE.match(d.name)),
            reverse=True,
        )
        removed: list[str] = []
        for stale in candidates[keep:]:
            shutil.rmtree(stale, ignore_errors=True)
            removed.append(stale.name)
        return {"removed": removed, "kept": len(candidates[:keep]), "disabled": False}

    def backup(self, requested: str | None = None) -> Operation:
        """把世界文件（可指定单个）与 serverconfig.txt 复制到 backup/<时间戳>/。"""
        filename = self.resolve(requested) if requested else None
        return self._operations.submit(
            "world.backup", lambda progress: self._backup_job(filename, progress)
        )

    def backup_now(self, requested: str | None = None) -> dict[str, object]:
        """同步备份（定时任务用；HTTP 触发的那条走操作框架）。"""
        filename = self.resolve(requested) if requested else None
        return self._backup_job(filename, lambda *_args: None)

    # -- 恢复 ---------------------------------------------------------
    def resolve_backup(self, name: str) -> Path:
        """把备份标识解析成磁盘上的文件，并挡住路径穿越。"""
        if name.startswith("auto:"):
            candidate = (self.worlds_dir / name[len("auto:") :]).resolve()
            if candidate.parent != self.worlds_dir.resolve() or not candidate.is_file():
                raise NotFound(f"备份不存在: {name}")
            return candidate

        if Path(name).name != name or not name:
            raise BadRequest("invalid backup name")
        directory = self.backup_dir / name
        if not directory.is_dir():
            raise NotFound(f"备份不存在: {name}")
        worlds = sorted(directory.glob("*.wld"))
        if not worlds:
            raise BadRequest(f"备份 {name} 里没有 .wld 文件")
        if len(worlds) > 1:
            raise BadRequest(
                f"备份 {name} 里有多个世界，请用 file 指定其中一个",
                details={"candidates": [p.name for p in worlds]},
            )
        return worlds[0]

    def restore(self, name: str, requested_file: str | None = None) -> Operation:
        source = self.resolve_backup(name) if not requested_file else self._pick_in_backup(name, requested_file)
        return self._operations.submit(
            "world.restore",
            lambda progress: self._restore_job(name, source, progress),
        )

    def _pick_in_backup(self, name: str, filename: str) -> Path:
        if Path(filename).name != filename:
            raise BadRequest("invalid filename")
        if name.startswith("auto:"):
            raise BadRequest("auto 备份只能恢复它自己")
        candidate = (self.backup_dir / name / filename).resolve()
        if not candidate.is_file():
            raise NotFound(f"备份 {name} 里没有 {filename}")
        return candidate

    def _restore_job(self, name: str, source: Path, progress) -> dict[str, object]:
        """恢复一个世界文件。

        覆盖「当前正在使用的世界」有个坑：容器会在进程退出后立刻重启，如果直接覆盖文件，
        服务端可能在启动读盘的中途被改写。所以走两段式：

            保存 → 存安全副本 → exit（起来后仍加载旧世界）→ 覆盖文件 → exit-nosave
            （起来后加载恢复后的世界）

        第二次必须用 exit-nosave，否则退出时的保存会把刚恢复的文件覆盖回去。
        """
        target = self.worlds_dir / _target_name(source)
        active = self.active_world_file()
        is_active = target.name == active

        progress(5, "保存当前世界")
        try:
            self._channel.send("save")
        except Exception as exc:  # noqa: BLE001
            raise UpstreamFailed(f"failed to save world: {exc}") from exc
        time.sleep(SAVE_GRACE)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safety = self.backup_dir / f"pre-restore-{stamp}"
        safety.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.copy2(target, safety / target.name)
        progress(15, f"安全副本已保存到 {safety.name}")

        expected = _sha256(source)

        if is_active:
            progress(25, "重启服务端（准备替换世界文件）")
            self._channel.send("exit")
            self._wait_until_up(progress, base=30, span=25)

            progress(60, "写入恢复后的世界文件")
            shutil.copy2(source, target)
            if _sha256(target) != expected:
                raise UpstreamFailed("写入后的文件校验失败")

            progress(70, "再次重启以加载恢复后的世界")
            self._channel.send("exit-nosave")
            self._wait_until_up(progress, base=75, span=20)
        else:
            progress(60, "写入世界文件（该世界当前未激活，无需重启）")
            shutil.copy2(source, target)
            if _sha256(target) != expected:
                raise UpstreamFailed("写入后的文件校验失败")

        self._status.invalidate()
        return {
            "restored": target.name,
            "from": name,
            "active": is_active,
            "size": target.stat().st_size,
            "sha256": expected,
            "safety_copy": str(safety),
        }

    def _wait_until_up(self, progress, *, base: int, span: int, timeout: float = 45.0) -> None:
        deadline = time.monotonic() + timeout
        step = 0
        while time.monotonic() < deadline:
            try:
                if "Terraria Server" in self._channel.run("version", timeout=2.0):
                    self._status.invalidate()
                    return
            except Exception:  # noqa: BLE001 - 重启窗口内允许失败
                pass
            step += 1
            progress(min(base + span, base + step * 3), "等待服务端重新监听")
            time.sleep(1)
        raise UpstreamFailed(f"服务端在 {int(timeout)} 秒内没有恢复")


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


def _target_name(source: Path) -> str:
    """备份文件对应的世界名。

    Terraria 自己的备份是 `xxx.wld.bak` / `.bak2`，恢复时要还原成 `xxx.wld`，
    否则会把备份恢复成一个新的「世界文件」而不是覆盖原世界。
    """
    name = source.name
    for suffix in (".bak2", ".bak"):
        if name.endswith(suffix) and name[: -len(suffix)].endswith(".wld"):
            return name[: -len(suffix)]
    return name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
