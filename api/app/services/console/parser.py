"""控制台回显解析。

旧实现把这些正则散落在 routers/server.py 里，且 `parse_players` 写在路由模块中。
集中到这里，并配 tests/test_parser.py 用真实日志片段做回归。
"""

from __future__ import annotations

import re

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
