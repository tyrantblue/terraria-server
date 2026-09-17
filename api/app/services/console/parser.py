"""控制台回显解析。

旧实现把这些正则散落在 routers/server.py 里，且 `parse_players` 写在路由模块中。
集中到这里，并配 tests/test_parser.py 用真实日志片段做回归。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RE_VERSION = re.compile(r"Terraria Server v(.+)")
RE_PORT = re.compile(r"Port:\s*(\d+)")
RE_MAX_PLAYERS = re.compile(r"Player limit:\s*(\d+)")
RE_TIME = re.compile(r"Time:\s*(.+)")
RE_SEED = re.compile(r"World Seed:\s*(.+)")
RE_MOTD = re.compile(r"MOTD:\s*(.*)")
RE_PLAYER_LINE = re.compile(r"^(.+?) \(.+:\d+\)$")

NO_PLAYERS = "No players connected."

#: 哨兵命令：不带参数的 kick 是无效调用，既不产生副作用，回显又是固定的
#: "Usage: kick <player>"。用它给「本次命令的回显」划一条确定的结束边界。
#: 这个字符串同时用于把哨兵行从控制台视图里过滤掉——因为是按文本判定，
#: 所以面板、守卫进程、甚至重启前的旧行都能被过滤干净。
SENTINEL_COMMAND = "kick"
FENCE_TEXT = "Usage: kick <player>"


def is_fence_line(text: str) -> bool:
    """这一行是不是哨兵的回显（不该出现在控制台视图里）。"""
    return FENCE_TEXT in text


def parse_version(text: str) -> str | None:
    match = RE_VERSION.search(text)
    return match.group(1).strip() if match else None


def parse_port(text: str) -> int | None:
    match = RE_PORT.search(text)
    return int(match.group(1)) if match else None


def parse_max_players(text: str) -> int | None:
    match = RE_MAX_PLAYERS.search(text)
    return int(match.group(1)) if match else None


def parse_game_time(text: str) -> str | None:
    match = RE_TIME.search(text)
    return match.group(1).strip() if match else None


def parse_seed(text: str) -> str | None:
    match = RE_SEED.search(text)
    return match.group(1).strip() if match else None


def parse_motd(text: str) -> str | None:
    match = RE_MOTD.search(text)
    return match.group(1).strip() if match else None


def parse_players(text: str) -> list[str]:
    """解析 `playing` 的回显，返回玩家名列表。

    与旧实现保持完全一致：逐行 strip、跳过空行与 "No players connected."，
    只认形如 `名字 (ip:port)` 的行。
    """
    players: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if NO_PLAYERS in line:
            continue
        match = RE_PLAYER_LINE.match(line)
        if match:
            players.append(match.group(1).strip())
    return players


# ---------------------------------------------------------------- 玩家条目
RE_PLAYER_ENTRY = re.compile(
    r"^(?P<name>.+?) \((?P<ip>[^()\s]+):(?P<port>\d+)\)\s*$"
)


@dataclass(frozen=True)
class PlayerEntry:
    """playing 里的一个玩家条目：`名字 (ip:port)`。"""

    name: str
    ip: str
    port: int


def parse_player_entries(text: str) -> list[PlayerEntry]:
    """比 parse_players 多解析出 IP 与端口（v1 的 /players 需要）。"""
    entries: list[PlayerEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = RE_PLAYER_ENTRY.match(line)
        if match:
            entries.append(
                PlayerEntry(
                    name=match.group("name").strip(),
                    ip=match.group("ip"),
                    port=int(match.group("port")),
                )
            )
    return entries


# ---------------------------------------------------------------- 日志分类
_CHAT = re.compile(r"^<?[^<>]+> ")
_SAVE_PREFIXES = ("Saving world data", "Validating world save", "Backing up world file")
_STARTUP_PREFIXES = (
    "Resetting game objects",
    "Loading world data",
    "Settling liquids",
    "Terraria Server v",
    "Listening on port",
    "Server started",
)


def classify_line(text: str) -> str:
    """把一行控制台日志归类，供 v1 的结构化控制台接口使用。

    面板不必再自己写正则去猜「这行是什么」。
    """
    stripped = text.strip()
    if not stripped:
        return "blank"
    if stripped in (":", ": "):
        return "prompt"

    # 日志里经常出现 ": 内容" 这种提示符与内容粘连的形态
    body = stripped[1:].strip() if stripped.startswith(":") else stripped

    if "has joined." in body:
        return "player_join"
    if "has left." in body:
        return "player_leave"
    if "is connecting..." in body:
        return "connect"
    if "lost connection..." in body:
        return "disconnect"
    if "was booted:" in body:
        return "boot"
    if body.startswith(_SAVE_PREFIXES):
        return "world_save"
    if body.startswith(_STARTUP_PREFIXES):
        return "startup"
    if (
        "Unhandled Exception" in body
        or "FATAL UNHANDLED" in body
        or "Invariant Failed" in body
        or "Error Logging Enabled" in body
    ):
        return "error"
    if _CHAT.match(body):
        return "chat"
    return "output"
