"""日志事件监听：把控制台日志里值得知道的事转成通知。

只从「当前末尾」开始跟，不回放历史，避免重启 API 时把旧事件重发一遍。
识别的事件：
    player_join / player_leave / player_booted / server_up / server_error
"""

from __future__ import annotations

import logging
import re
import threading
import time

from app.services.console.log_reader import LogReader
from app.services.console.parser import split_line
from app.services.notifications import Notifier

logger = logging.getLogger(__name__)

RE_JOIN = re.compile(r"^(?P<name>.+?) has joined\.\s*$")
RE_LEAVE = re.compile(r"^(?P<name>.+?) has left\.\s*$")
RE_BOOT = re.compile(r"^(?P<ip>[\d.]+):(?P<port>\d+) was booted:\s*(?P<reason>.*?)\s*$")
RE_UP = re.compile(r"Listening on port (\d+)")
ERROR_MARKERS = ("Unhandled Exception", "FATAL UNHANDLED", "Invariant Failed")
#: 崩溃输出是一整段堆栈，短时间内只发一次
ERROR_COOLDOWN = 120.0


class LogEventWatcher(threading.Thread):
    def __init__(
        self,
        reader: LogReader,
        notifier: Notifier,
        *,
        interval: float = 1.0,
    ) -> None:
        super().__init__(name="log-events", daemon=True)
        self.reader = reader
        self.notifier = notifier
        self.interval = interval
        self._stopped = threading.Event()
        self._cursor: int | None = None
        self._last_error = 0.0

    def stop(self) -> None:
        self._stopped.set()

    def run(self) -> None:  # pragma: no cover - 线程体
        self._cursor = self.reader.size()
        while not self._stopped.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.warning("log-events: %s", exc)
            self._stopped.wait(self.interval)

    def _tick(self) -> None:
        size = self.reader.size()
        if size < (self._cursor or 0):  # 日志被轮转/截断
            self._cursor = 0
        self._cursor, lines = self.reader.read_lines_with_offsets(self._cursor or 0)
        for _offset, text in lines:
            self.handle(text)

    # 供测试直接调用
    def handle(self, text: str) -> str | None:
        # 先剥掉 start.sh 可能加上的 [时间戳] 与服务端提示符，再识别
        _stamp, body = split_line(text)
        body = body.strip()

        match = RE_JOIN.match(body)
        if match:
            self.notifier.notify(
                "player_join", f"{match.group('name')} 加入了游戏", level="success"
            )
            return "player_join"

        match = RE_LEAVE.match(body)
        if match:
            self.notifier.notify("player_leave", f"{match.group('name')} 离开了游戏")
            return "player_leave"

        match = RE_BOOT.match(body)
        if match:
            self.notifier.notify(
                "player_booted",
                f"{match.group('ip')} 被拒绝",
                level="warning",
                detail={"reason": match.group("reason")},
            )
            return "player_booted"

        if RE_UP.search(body):
            self.notifier.notify("server_up", "服务端已启动/重启完成", level="success")
            return "server_up"

        if any(marker in body for marker in ERROR_MARKERS):
            now = time.time()
            if now - self._last_error > ERROR_COOLDOWN:
                self._last_error = now
                self.notifier.notify(
                    "server_error",
                    "服务端出现异常（详见控制台日志）",
                    level="error",
                    detail={"line": body[:120]},
                )
            return "server_error"

        return None
