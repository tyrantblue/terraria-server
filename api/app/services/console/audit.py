"""控制台命令白名单 + 审计。

旧接口 `POST /api/server/command` 是任意命令直通：没有白名单、没有权限、
没有审计。v1 的 `POST /api/v1/console/commands` 收紧为「白名单 + 记录谁在什么时候
发了什么」，并且**不允许** exit/exit-nosave —— 重启请走
`POST /api/v1/server/restart`，它带保存与等待恢复。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from app.core.errors import BadRequest, Forbidden

#: 允许透传的控制台命令（按第一个词匹配）
ALLOWED_COMMANDS = {
    "help",
    "save",
    "settle",
    "playing",
    "version",
    "port",
    "maxplayers",
    "time",
    "seed",
    "motd",
    "password",
    "kick",
    "ban",
    "say",
    "dawn",
    "noon",
    "dusk",
    "midnight",
}

#: 明确禁止：关服请用重启操作，避免绕过保存与恢复等待
FORBIDDEN_COMMANDS = {"exit", "exit-nosave", "off", "quit"}


@dataclass(frozen=True)
class AuditEntry:
    ts: float
    command: str
    actor: str


class AuditLog:
    def __init__(self, capacity: int = 200) -> None:
        self._entries: deque[AuditEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def record(self, command: str, actor: str) -> None:
        with self._lock:
            self._entries.append(AuditEntry(ts=time.time(), command=command, actor=actor))

    def entries(self) -> list[AuditEntry]:
        with self._lock:
            return list(reversed(self._entries))


def guard_command(command: str) -> str:
    """校验控制台命令是否在白名单内，返回规范化后的命令。"""
    command = command.strip()
    if not command:
        raise BadRequest("command cannot be empty")
    if "\n" in command or "\r" in command:
        raise BadRequest("command must be a single line")

    head = command.split(maxsplit=1)[0].lower()
    if head in FORBIDDEN_COMMANDS:
        raise Forbidden(
            f"不允许通过该接口执行 {head}；请改用 POST /api/v1/server/restart",
            details={"allowed": sorted(ALLOWED_COMMANDS)},
        )
    if head not in ALLOWED_COMMANDS:
        raise Forbidden(
            f"命令 {head} 不在白名单内",
            details={"allowed": sorted(ALLOWED_COMMANDS)},
        )
    return command
