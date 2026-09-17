"""控制台通道：把命令写进 FIFO，并可靠地取回需要回显的那部分命令的输出。

旧实现（api/app/services/terraria.py）的做法是「记录日志文件大小 → 写命令 →
等文件变大 → 把新增内容当作本次回显」，有三个硬伤：

1. 没有任何请求/响应边界：期间任何写进日志的内容（别的请求的命令回显、玩家进出、
   保存进度）都会被当成自己的输出；
2. 无法判断回显是否**已经结束**，只要文件一变就返回，可能拿到半行；
3. 还有一个跨进程的第二个写者（guard/terraria-watchd.py 也写 FIFO），
   两边用的是同样的脆弱逻辑，会互相读串。

修法（已在本机实测可行）：

* 进程内用 threading.Lock 串行；跨进程用 control/console.lock 上的 flock 串行
  （API 与守卫进程共用同一把锁）；
* 需要回显时，命令后面紧跟一条**哨兵命令**：不带参数的 `kick`。它是无效调用
  （无副作用），回显固定为 `Usage: kick <player>`，于是「读到哨兵那一行」就是
  本次回显的确定性边界；
* 哨兵行由控制台视图按文本过滤掉（parser.is_fence_line），所以按文本判定即可
  覆盖守卫进程产生的哨兵行、以及重启前留在日志里的旧行。

实测回显形状：

    写入  "playing\nkick\n"
    日志  "No players connected.\n: Usage: kick <player>\n: "
    → 边界前的内容就是 playing 的回显，与旧实现拿到的内容一致。

单向命令（save / settle / exit / say / kick …）走 send()：只写不读，与旧实现
完全一致——`save` 会阻塞服务端主循环好几秒，等它的哨兵只会平白超时。
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from app.core.errors import ConsoleBusy, ConsoleTimeout, ConsoleUnavailable, ValidationFailed
from app.services.console.log_reader import LogReader
from app.services.console.parser import FENCE_TEXT, SENTINEL_COMMAND

logger = logging.getLogger(__name__)

_FENCE = FENCE_TEXT.encode()


@contextmanager
def exclusive_lock(path: Path, timeout: float):
    """基于 flock 的跨进程互斥（不同容器共享内核，同一挂载目录下可用）。"""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    acquired = False
    try:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise ConsoleBusy("控制台正忙（等待锁超时），请稍后重试") from exc
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:  # pragma: no cover
                pass
        os.close(fd)


def _validate(command: str) -> str:
    command = command.strip()
    if not command:
        raise ValidationFailed("command cannot be empty")
    if "\n" in command or "\r" in command:
        raise ValidationFailed("command must be a single line")
    return command


class ConsoleChannel:
    """串行化的 FIFO 命令通道。"""

    def __init__(
        self,
        *,
        fifo: Path,
        reader: LogReader,
        lock_path: Path,
        sentinel: str = SENTINEL_COMMAND,
        timeout: float = 3.0,
        lock_timeout: float = 5.0,
        poll_interval: float = 0.05,
    ) -> None:
        self.fifo = fifo
        self.reader = reader
        self.lock_path = lock_path
        self.sentinel = sentinel
        self.timeout = timeout
        self.lock_timeout = lock_timeout
        self.poll_interval = poll_interval
        self._gate = threading.Lock()
        self._degraded = 0

    # -- 对外 ---------------------------------------------------------
    @property
    def degraded_count(self) -> int:
        """哨兵没按时出现（回退到部分输出）的次数，用于观测。"""
        return self._degraded

    def send(self, command: str) -> None:
        """单向命令：只写不读。用于 save / settle / exit / say / kick 等。

        与旧实现一致：不等待任何回显，因此 `save` 阻塞主循环也不会误判超时。
        """
        command = _validate(command)
        with self._gate:
            with exclusive_lock(self.lock_path, self.lock_timeout):
                self._write_payload(f"{command}\n".encode())

    def run(self, command: str, timeout: float | None = None) -> str:
        """执行一条需要回显的命令，返回它的输出（不含哨兵与提示符残留）。"""
        command = _validate(command)
        with self._gate:
            return self._run_locked(command, self.timeout if timeout is None else timeout)

    # -- 内部 ---------------------------------------------------------
    def _run_locked(self, command: str, timeout: float) -> str:
        with exclusive_lock(self.lock_path, self.lock_timeout):
            start = self.reader.size()
            self._write_payload(f"{command}\n{self.sentinel}\n".encode())
            data, fence_start, fence_end = self._await_fence(start, timeout)

        if fence_start is None:
            self._degraded += 1
            logger.warning(
                "console sentinel not seen within %.1fs for %r; returning raw output",
                timeout,
                command,
            )
            text = data.decode("utf-8", errors="replace")
            if not text:
                raise ConsoleTimeout(f"命令 {command!r} 没有回显（服务端可能正在重启）")
            return text

        return data[: fence_start - start].decode("utf-8", errors="replace")

    def _write_payload(self, payload: bytes) -> None:
        try:
            fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            raise ConsoleUnavailable(
                "无法写入控制 FIFO（Terraria 容器可能正在重启）"
            ) from exc
        try:
            os.write(fd, payload)
        except OSError as exc:  # pragma: no cover - 极少数竞态
            raise ConsoleUnavailable(f"写入控制 FIFO 失败: {exc}") from exc
        finally:
            os.close(fd)

    def _await_fence(self, start: int, timeout: float) -> tuple[bytes, int | None, int]:
        """轮询日志直到出现哨兵行。返回 (新增数据, 哨兵行起始偏移, 结束偏移)。"""
        deadline = time.monotonic() + timeout
        data = b""
        while True:
            size = self.reader.size()
            if size > start:
                data = self.reader.read_bytes(start, size)
            index = data.rfind(_FENCE)
            if index != -1:
                line_start = data.rfind(b"\n", 0, index) + 1
                line_end = data.find(b"\n", index)
                line_end = len(data) if line_end == -1 else line_end + 1
                return data, start + line_start, start + line_end
            if time.monotonic() >= deadline:
                return data, None, 0
            time.sleep(self.poll_interval)
