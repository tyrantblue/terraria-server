"""封禁名单（banlist.txt）。

原版控制台只有 `ban <player>`，**没有 unban**，而且只会给「当前在线」的玩家写名单；
文件位置默认是存档目录下的 banlist.txt（本项目即 /worlds/banlist.txt），
在第一次封禁之前根本不存在。

因此这里的设计是「防御式」的：
* 读：文件不存在就返回空列表，不猜测格式之外的任何东西；
* 删：按行匹配移除，其余内容原样保留；不改变文件其它部分。

并发（issue #17.4）：删是「按行重写 + 原子替换」，两个并发 DELETE 若共用固定的
`.tmp` 名会互相覆盖。现在临时名带随机后缀，并用 `banlist.txt.lock` 上的 flock
串行化 API 侧的读-改-写，replace 前还会再确认文件没变。游戏进程自己的追加不持
这把锁，所以 **API 侧并发不丢行**；与游戏进程之间仍存在极小的窗口，这是文件协议
的固有限制（已写进 docs/api/v1.md §3 与根 README §29.6）。
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
        for line in self._read_text(path).splitlines():
            name = line.strip()
            if name and not name.startswith("#"):
                names.append(name)
        return names

    def exists(self) -> bool:
        return self.path.is_file()

    # -- 写 -----------------------------------------------------------
    def remove(self, name: str) -> bool:
        """按行移除一个名字。返回是否真的改动了文件。

        「唯一临时名 + flock」保证了 **API 侧**并发不丢行。但游戏进程自己往
        `banlist.txt` 追加时不持这把锁，所以读-改-写窗口里的追加仍可能被覆盖。
        这里通过「replace 前再读一次确认文件没变，变了就重来」把窗口从
        「读+过滤+写」缩短到「最后一次读 → os.replace」这一段，并最多重试几轮。
        这不能 100% 消除竞态（文件协议的固有限制），但把丢行概率降到极低。
        """
        with self._exclusive():
            path = self.path
            if not path.is_file():
                return False
            target = name.strip()
            latest = ""
            for attempt in range(5):
                original = self._read_text(path)
                lines = original.splitlines()
                kept = [line for line in lines if line.strip() != target]
                if len(kept) == len(lines):
                    return False
                text = "\n".join(kept) + ("\n" if kept else "")
                latest = self._read_text(path)
                if latest == original:
                    self._write_atomic(text)
                    return True
                if attempt == 4:
                    # 外部一直在追加：用最新内容再过滤一次后尽力写入
                    lines = latest.splitlines()
                    kept = [line for line in lines if line.strip() != target]
                    self._write_atomic("\n".join(kept) + ("\n" if kept else ""))
                    return True
            return True

    def append(self, name: str) -> None:
        """给测试/运维用：在锁内追加一行（游戏进程自己追加时不走这里）。"""
        with self._exclusive():
            path = self.path
            existing = self._read_text(path) if path.exists() else ""
            if existing and not existing.endswith("\n"):
                existing += "\n"
            self._write_atomic(f"{existing}{name}\n")

    # -- 内部 ---------------------------------------------------------
    def _read_text(self, path: Path) -> str:
        """单独抽出来便于测试注入「不合作的追加者」（见 test_issue_17）。"""
        return path.read_text(encoding="utf-8", errors="replace")

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
