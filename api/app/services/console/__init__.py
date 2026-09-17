"""控制台访问层：FIFO 通道 + 日志读取 + 回显解析。"""

from app.services.console.audit import AuditLog, guard_command
from app.services.console.channel import ConsoleChannel, exclusive_lock
from app.services.console.log_reader import LogReader
from app.services.console.parser import (
    FENCE_TEXT,
    SENTINEL_COMMAND,
    PlayerEntry,
    classify_line,
    is_fence_line,
    parse_player_entries,
)

__all__ = [
    "AuditLog",
    "ConsoleChannel",
    "guard_command",
    "exclusive_lock",
    "LogReader",
    "FENCE_TEXT",
    "SENTINEL_COMMAND",
    "PlayerEntry",
    "classify_line",
    "is_fence_line",
    "parse_player_entries",
]
