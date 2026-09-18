"""日志读取：尾部读取与增量行读取。

要点：
* 绝不把整个 output.log 读进内存（旧实现用 readlines() 全量读）。
* 只返回**完整行**，未以换行结尾的尾部留给下次，避免把半行发出去。
* 感知日志被轮转/截断（偏移变小 → 从头开始）。
* 同时提供「带字节偏移」的版本，供 v1 的结构化控制台做增量游标。
* 哨兵行的过滤由调用方用 parser.is_fence_line() 完成（按文本判定，
  因此守卫进程产生的哨兵行、以及重启前留下的旧行也能被过滤）。
"""

from __future__ import annotations

import time
from pathlib import Path

#: tail 单次最多回读的字节数，避免日志很大时把整文件读进来
TAIL_MAX_BYTES = 256 * 1024

Line = tuple[int, str]


class LogReader:
    """对一个追加写日志文件的轻量读取器。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    # -- 基础 ---------------------------------------------------------
    def exists(self) -> bool:
        return self.path.exists()

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def age(self) -> float | None:
        """日志最后一次写入距今多少秒（文件不存在返回 None）。"""
        try:
            return max(0.0, time.time() - self.path.stat().st_mtime)
        except OSError:
            return None

    def read_bytes(self, offset: int, end: int | None = None) -> bytes:
        try:
            with self.path.open("rb") as fh:
                fh.seek(offset)
                return fh.read() if end is None else fh.read(max(0, end - offset))
        except OSError:
            return b""

    # -- 增量读取 -----------------------------------------------------
    def read_lines_with_offsets(self, offset: int) -> tuple[int, list[Line]]:
        """从 offset 开始读出所有完整行，返回 (新偏移, [(行起始偏移, 文本)])。"""
        size = self.size()
        if size <= offset:
            return offset, []
        data = self.read_bytes(offset, size)
        cut = data.rfind(b"\n")
        if cut == -1:
            return offset, []

        chunk = data[: cut + 1]
        lines: list[Line] = []
        cursor = offset
        for raw in chunk.split(b"\n")[:-1]:
            lines.append((cursor, raw.decode("utf-8", errors="replace")))
            cursor += len(raw) + 1
        return cursor, lines

    def read_lines(self, offset: int) -> tuple[int, list[str]]:
        cursor, lines = self.read_lines_with_offsets(offset)
        return cursor, [text for _offset, text in lines]

    # -- 尾部读取（/api/server/console 用） ---------------------------
    def tail_with_offsets(self, count: int, max_bytes: int = TAIL_MAX_BYTES) -> list[Line]:
        """返回最后 count 行的 (偏移, 文本)，只回读文件尾部至多 max_bytes 字节。"""
        if count <= 0:
            return []
        size = self.size()
        start = max(0, size - max_bytes)
        data = self.read_bytes(start, size)
        if start > 0:
            # 丢掉可能被截断的首行
            first_break = data.find(b"\n")
            if first_break == -1:
                return []
            start += first_break + 1
            data = data[first_break + 1 :]
        cut = data.rfind(b"\n")
        if cut == -1:
            return []

        lines: list[Line] = []
        cursor = start
        for raw in data[: cut + 1].split(b"\n")[:-1]:
            lines.append((cursor, raw.decode("utf-8", errors="replace")))
            cursor += len(raw) + 1
        return lines[-count:]

    def tail(self, count: int) -> list[str]:
        """尾部行文本（不含偏移）。"""
        return [text for _offset, text in self.tail_with_offsets(count)]
