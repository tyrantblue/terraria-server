"""控制台命令白名单 + 审计。

旧接口 `POST /api/server/command` 是任意命令直通：没有白名单、没有权限、
没有审计。v1 的 `POST /api/v1/console/commands` 收紧为「白名单 + 记录谁在什么时候
发了什么」，并且**不允许** exit/exit-nosave —— 重启请走
`POST /api/v1/server/restart`，它带保存与等待恢复。

审计从 2.0.0 起**落盘**（issue #3）：`control/audit.log`，一行一条 JSON
（`{ts, actor, command, result}`），配合 `ops/logrotate.terraria` 的 copytruncate 轮转。
内存里仍保留最近 `capacity` 条用于快速读取；带 `?tail=N` 时从文件回读，
所以 API 重启之后依然能查到「上周谁改了什么」。

落盘失败（磁盘满、目录只读）**不会**让控制台命令失败，但会记一条 warning：
审计是旁路，不该成为控制台的新故障点。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import BadRequest, Forbidden

logger = logging.getLogger(__name__)

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

#: 单条审计里保留的回显字符数（回显可能很长，审计不是日志转储）
RESULT_MAX_CHARS = 500
#: 从文件回读时最多扫过的字节数，避免审计文件很大时把整文件读进内存
FILE_READ_MAX_BYTES = 512 * 1024
#: `?tail=N` 的上限
TAIL_MAX = 1000


@dataclass(frozen=True)
class AuditEntry:
    ts: float
    command: str
    actor: str
    result: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts,
            "actor": self.actor,
            "command": self.command,
            "result": self.result,
        }


def _trim(result: str | None) -> str | None:
    if result is None:
        return None
    text = result.strip()
    if len(text) <= RESULT_MAX_CHARS:
        return text
    return text[:RESULT_MAX_CHARS] + "…"


class AuditLog:
    """控制台命令审计：内存缓存 + 追加写文件。"""

    def __init__(self, path: Path | None = None, capacity: int = 200) -> None:
        self.path = path
        self._entries: deque[AuditEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    # -- 写 -----------------------------------------------------------
    def record(self, command: str, actor: str, *, result: str | None = None) -> AuditEntry:
        entry = AuditEntry(
            ts=time.time(), command=command, actor=actor, result=_trim(result)
        )
        with self._lock:
            self._entries.append(entry)
        self._append(entry)
        return entry

    def _append(self, entry: AuditEntry) -> None:
        if self.path is None:
            return
        line = json.dumps(entry.as_dict(), ensure_ascii=False) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            logger.warning("审计落盘失败（%s）：%s", self.path, exc)

    # -- 读 -----------------------------------------------------------
    def entries(self, tail: int | None = None) -> list[AuditEntry]:
        """`tail=None` 读内存缓存（最近 capacity 条）；给了 N 就从文件回读最近 N 条。

        文件被 logrotate 轮转/截断、或 API 刚重启时，回读可能比内存少，
        此时按文件里的实际内容返回（审计的价值在于「有什么」而不是「应该有什么」）。
        """
        if tail is None:
            with self._lock:
                return list(reversed(self._entries))
        entries = self._read_file()
        if not entries:
            with self._lock:
                return list(reversed(self._entries))[-tail:]
        return list(reversed(entries[-tail:]))

    def _read_file(self) -> list[AuditEntry]:
        if self.path is None:
            return []
        try:
            size = self.path.stat().st_size
            with self.path.open("rb") as handle:
                if size > FILE_READ_MAX_BYTES:
                    handle.seek(size - FILE_READ_MAX_BYTES)
                    # 从中间截断的首行丢掉，可能是半条 JSON
                    handle.readline()
                data = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return []

        entries: list[AuditEntry] = []
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue  # 半截行/手工改坏的行跳过，不影响其它条目
            if not isinstance(payload, dict):
                continue
            entries.append(
                AuditEntry(
                    ts=float(payload.get("ts") or 0.0),
                    command=str(payload.get("command") or ""),
                    actor=str(payload.get("actor") or ""),
                    result=payload.get("result"),
                )
            )
        return entries


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
