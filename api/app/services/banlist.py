"""封禁名单（banlist.txt）。

原版控制台只有 `ban <player>`，**没有 unban**，而且只会给「当前在线」的玩家写名单；
文件位置默认是存档目录下的 banlist.txt（本项目即 /worlds/banlist.txt），
在第一次封禁之前根本不存在。

因此这里的设计是「防御式」的：
* 读：文件不存在就返回空列表，不猜测格式之外的任何东西；
* 删：按行匹配移除，其余内容原样保留；不改变文件其它部分。

并发（issue #17.4）：删是「按行重写 + 原子替换」，两个并发 DELETE 若共用固定的
`.tmp` 名会互相覆盖。现在临时名带随机后缀，并用 `banlist.txt.lock` 上的 flock
串行化 API 侧的读-改-写。游戏进程自己的追加不持这把锁，所以只能保证 API 内部
不丢行——这是文件协议的固有限制，文档里已注明。
"""

from __future__ import annotations

import fcntl
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path


class BanList:
    def __init__(self, worlds_dir: Path, config_path: Path | None = None) -> None:
        self.worlds_dir = worlds_dir
        self.config_path = config_path
        #: 进程内串行（flock 只保证跨进程，同一进程内多线程要自己加锁）
        self._lock = threading.Lock()

    # -- 路径 ---------------------------------------------------------
    @property
    def path(self) -> Path:
        """优先用 serverconfig.txt 里的 banlist= 配置，否则用存档目录下的默认位置。"""
        if self.config_path and self.config_path.exists():
            for line in self.config_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("banlist=") and not stripped.startswith("#"):
                    value = stripped.split("=", 1)[1].strip()
                    if value:
                        candidate = Path(value)
                        if not candidate.is_absolute():
                            candidate = self.worlds_dir / candidate
                        return candidate
        return self.worlds_dir / "banlist.txt"

    # -- 读 -----------------------------------------------------------
    def entries(self) -> list[str]:
        path = self.path
        if not path.is_file():
            return []
        names: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            name = line.strip()
            if name and not name.startswith("#"):
                names.append(name)
        return names

    def exists(self) -> bool:
        return self.path.is_file()

    # -- 写 -----------------------------------------------------------
    def remove(self, name: str) -> bool:
        """按行移除一个名字。返回是否真的改动了文件。"""
        with self._exclusive():
            path = self.path
            if not path.is_file():
                return False
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            kept = [line for line in lines if line.strip() != name.strip()]
            if len(kept) == len(lines):
                return False
            self._write_atomic("\n".join(kept) + ("\n" if kept else ""))
            return True

    def append(self, name: str) -> None:
        """给测试/运维用：在锁内追加一行（游戏进程自己追加时不走这里）。"""
        with self._exclusive():
            path = self.path
            existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            if existing and not existing.endswith("\n"):
                existing += "\n"
            self._write_atomic(f"{existing}{name}\n")

    def _write_atomic(self, text: str) -> None:
        # 唯一临时名：并发写不再共用同一个 `.tmp`（issue #17.4）
        tmp = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    @contextmanager
    def _exclusive(self):
        """进程内线程锁 + 跨进程 flock（锁文件不可用时退化为仅线程锁）。"""
        with self._lock:
            lock_path = self.path.with_name(self.path.name + ".lock")
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            except OSError:
                yield
                return
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:  # pragma: no cover
                    pass
                os.close(fd)
