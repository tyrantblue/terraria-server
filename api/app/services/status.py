"""服务器状态缓存。

旧实现：每次 GET /api/server/status 都串行发 7 条控制台命令（version/port/
maxplayers/time/seed/motd/playing），每条最多等 2s，并且每发一次就在日志里多留
几行横幅——实测日志约 65% 的行都是这个轮询刷出来的。

新实现：静态字段（version/port/maxplayers/seed/motd）与动态字段（time/players）
分开缓存。静态字段只在启动、我们改过配置、或从日志里看到服务端重启时才刷新；
动态字段按 TTL（默认 3s）刷新。面板的轮询不再放大成控制台命令风暴。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from app.services.console.channel import ConsoleChannel
from app.services.console.log_reader import LogReader
from app.services.console.parser import (
    PlayerEntry,
    parse_game_time,
    parse_max_players,
    parse_motd,
    parse_player_entries,
    parse_players,
    parse_port,
    parse_seed,
    parse_version,
)

RESTART_MARKER = "Listening on port"


@dataclass
class ServerSnapshot:
    """与旧 /api/server/status 响应一一对应的领域对象。"""

    running: bool = True
    version: str | None = None
    port: int | None = None
    max_players: int | None = None
    game_time: str | None = None
    seed: str | None = None
    motd: str | None = None
    players: list[str] = field(default_factory=list)
    #: v1 用：同上，但带 IP/端口
    player_entries: list[PlayerEntry] = field(default_factory=list)


class StatusCollector:
    def __init__(
        self,
        channel: ConsoleChannel,
        reader: LogReader,
        *,
        ttl: float = 3.0,
        static_ttl: float = 300.0,
    ) -> None:
        self._channel = channel
        self._reader = reader
        self._ttl = ttl
        self._static_ttl = static_ttl
        self._lock = threading.RLock()
        self._static: dict[str, object] = {}
        self._players: list[str] = []
        self._player_entries: list[PlayerEntry] = []
        self._game_time: str | None = None
        self._static_at = 0.0
        self._dynamic_at = 0.0
        self._log_cursor: int | None = None

    # -- 对外 ---------------------------------------------------------
    def invalidate(self, *, static: bool = True, dynamic: bool = True) -> None:
        with self._lock:
            if static:
                self._static = {}
                self._static_at = 0.0
            if dynamic:
                self._players = []
                self._player_entries = []
                self._game_time = None
                self._dynamic_at = 0.0

    def get(self) -> ServerSnapshot:
        with self._lock:
            self._watch_log()
            now = time.monotonic()
            if not self._static or now - self._static_at >= self._static_ttl:
                self._refresh_static()
            if now - self._dynamic_at >= self._ttl:
                self._refresh_dynamic()
            return self._snapshot()

    def players(self) -> list[str]:
        with self._lock:
            self._watch_log()
            if time.monotonic() - self._dynamic_at >= self._ttl:
                self._refresh_dynamic()
            return list(self._players)

    def player_entries(self) -> list[PlayerEntry]:
        """v1 用：带 IP/端口的玩家列表。"""
        with self._lock:
            self._watch_log()
            if time.monotonic() - self._dynamic_at >= self._ttl:
                self._refresh_dynamic()
            return list(self._player_entries)

    # -- 内部 ---------------------------------------------------------
    def _snapshot(self) -> ServerSnapshot:
        return ServerSnapshot(
            version=self._static.get("version"),  # type: ignore[arg-type]
            port=self._static.get("port"),  # type: ignore[arg-type]
            max_players=self._static.get("max_players"),  # type: ignore[arg-type]
            game_time=self._game_time,
            seed=self._static.get("seed"),  # type: ignore[arg-type]
            motd=self._static.get("motd"),  # type: ignore[arg-type]
            players=list(self._players),
            player_entries=list(self._player_entries),
        )

    def _refresh_static(self) -> None:
        try:
            static = {
                "version": parse_version(self._channel.run("version")),
                "port": parse_port(self._channel.run("port")),
                "max_players": parse_max_players(self._channel.run("maxplayers")),
                "seed": parse_seed(self._channel.run("seed")),
                "motd": parse_motd(self._channel.run("motd")),
            }
        except Exception:
            # 服务端正在重启/控制台不可用时，尽量返回上一次的缓存值；
            # 完全没有缓存才把错误抛给调用方。
            if not self._static:
                raise
            return
        self._static = static
        self._static_at = time.monotonic()

    def _refresh_dynamic(self) -> None:
        try:
            game_time = parse_game_time(self._channel.run("time"))
            playing = self._channel.run("playing")
            players = parse_players(playing)
            entries = parse_player_entries(playing)
        except Exception:
            if not self._dynamic_at:
                raise
            return
        self._game_time = game_time
        self._players = players
        self._player_entries = entries
        self._dynamic_at = time.monotonic()

    def _watch_log(self) -> None:
        """日志被截断、或出现「重新监听」时，说明服务端重启过 → 缓存作废。"""
        size = self._reader.size()
        if self._log_cursor is None:
            # 首次调用不做历史回放（历史里的重启标记没有意义）
            self._log_cursor = size
            return
        if size < self._log_cursor:
            self._log_cursor = size
            self.invalidate()
            return
        cursor, lines = self._reader.read_lines(self._log_cursor)
        self._log_cursor = cursor
        for text in lines:
            if RESTART_MARKER in text:
                self.invalidate()
                return
