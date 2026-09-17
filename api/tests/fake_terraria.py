"""测试用的假 Terraria：读 FIFO、按真实格式写日志。

真实回显的形状（实测）是每条命令输出 "<文本>\\n: "，所以两条连续命令在日志里
长这样：

    No players connected.
    : Invalid command.
    :

这个 fixture 精确复现该形状，于是哨兵栅栏、噪音过滤、解析器都能在不启动真服务端
的情况下被测到。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

#: 与 app/core/settings.py / services/console/parser.py 保持一致
SENTINEL = "kick"

DEFAULT_RESPONSES = {
    "version": "Terraria Server v1.4.5.8",
    "port": "Port: 7777",
    "maxplayers": "Player limit: 255",
    "time": "Time: 4:20 PM",
    "seed": "World Seed: 3.3.1.0.1269458679",
    "motd": "MOTD: Fly to the sky…Fire bird! Join the RED TEAM, PLZ!!!",
    "playing": "No players connected.",
    # 哨兵命令的回显：不带参数的 kick
    SENTINEL: "Usage: kick <player>",
}

#: 这些命令在真实服务端不会产生需要读取的回显（调用方也用 send() 发它们）
SILENT_PREFIXES = ("say ", "kick ", "ban ", "password ", "maxplayers ")
SILENT_EXACT = {"save", "settle", "exit", "dawn", "noon", "dusk", "midnight"}


class FakeTerraria(threading.Thread):
    def __init__(
        self,
        fifo: Path,
        log: Path,
        *,
        sentinel: str = SENTINEL,
        responses: dict[str, str] | None = None,
        players: tuple[str, ...] = (),
        startup_lines: bool = True,
    ) -> None:
        super().__init__(daemon=True)
        self.fifo = fifo
        self.log = log
        self.sentinel = sentinel
        self.responses = dict(DEFAULT_RESPONSES)
        if responses:
            self.responses.update(responses)
        self.players = players
        self._stop = threading.Event()
        if startup_lines:
            self._append("Error Logging Enabled.")
            self._append("Terraria Server v1.4.5.8")
            self._append("Listening on port 7777")
            self._append(": Server started")

    # -- 线程 ---------------------------------------------------------
    def run(self) -> None:  # pragma: no cover - 线程体
        fd = os.open(self.fifo, os.O_RDWR | os.O_NONBLOCK)
        buffer = b""
        try:
            while not self._stop.is_set():
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    time.sleep(0.01)
                    continue
                except OSError:
                    break
                if not chunk:
                    time.sleep(0.01)
                    continue
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    self._handle(raw.decode("utf-8", errors="replace").strip())
        finally:
            os.close(fd)

    def stop(self) -> None:
        self._stop.set()

    # -- 行为 ---------------------------------------------------------
    def _handle(self, command: str) -> None:
        # 注意：哨兵（裸 kick）走 responses 表，回显是 "Usage: kick <player>"；
        # 带参数的 kick（真正踢人）走下面的 SILENT 分支，不产生需要读取的回显。
        if command in SILENT_EXACT or command.startswith(SILENT_PREFIXES):
            return
        if command == "playing" and self.players:
            listing = "\n".join(self.players)
            self._append(f"{len(self.players)} players connected.\n{listing}\n: ")
            return
        text = self.responses.get(command, "Invalid command.")
        self._append(f"{text}\n: ")

    def _append(self, text: str) -> None:
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()

    # -- 测试便利方法 --------------------------------------------------
    def inject_line(self, text: str) -> None:
        """模拟「服务端自己写的一行日志」（玩家进出、保存进度等）。"""
        self._append(text)
