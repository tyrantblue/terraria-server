"""应用配置。

刻意不引入 pydantic-settings：这里只有少量常量，用 dataclass + 环境变量即可，
少一个依赖、少一份 lock 变更。所有路径都可通过环境变量覆盖，方便测试。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


#: 对外 API 版本。与 api/openapi.json 快照、docs/api/CHANGELOG.md 对应。
#: 破坏性变更必须升 major，并在这里改。
#: 2.0.0：删除旧 `/api/*` 资源路由，并把 GET 配置里的明文密码改成掩码。
API_VERSION = os.environ.get("TERRARIA_API_VERSION", "2.0.0")

#: 日志行首时间戳所用时区（与 terraria 容器的 TZ 一致）。
#: 必须显式带上，否则 naive datetime 会按进程本地时区解释，ts 会整体偏移。
LOG_TIMEZONE = os.environ.get("TERRARIA_LOG_TZ", os.environ.get("TZ", "Asia/Shanghai"))

#: 前端构建时对应的 API 版本；后端比它高太多时前端应提示用户刷新面板。
#: 2.0.0 起旧 `/api/*` 已删除，只有迁完 v1 的面板（1.4.0+）能正常工作，
#: 所以这里从 1.0.0 提到 1.4.0：更旧的面板会在握手上收到明确的升级提示，
#: 而不是运行到一半遇到 404。
MIN_CLIENT_VERSION = os.environ.get("TERRARIA_MIN_CLIENT_VERSION", "1.4.0")


@dataclass(frozen=True)
class Settings:
    """运行时路径与超时。字段默认值在 import 时从环境变量读取一次。"""

    worlds_dir: Path = _env_path("TERRARIA_WORLDS_DIR", "/opt/terraria/worlds")
    config_file: Path = _env_path("TERRARIA_CONFIG_FILE", "/opt/terraria/config/serverconfig.txt")
    control_dir: Path = _env_path("TERRARIA_CONTROL_DIR", "/opt/terraria/control")
    backup_dir: Path = _env_path("TERRARIA_BACKUP_DIR", "/opt/terraria/backup")

    api_version: str = API_VERSION
    min_client_version: str = MIN_CLIENT_VERSION

    #: 状态缓存 TTL（秒）：/time 与 /players 这类会变的字段多久刷新一次
    status_ttl: float = _env_float("TERRARIA_STATUS_TTL", 3.0)
    #: 静态字段（version/port/maxplayers/seed/motd）的兜底刷新间隔（秒）
    static_ttl: float = _env_float("TERRARIA_STATIC_TTL", 300.0)

    #: 单条控制台命令等待回显的超时（秒）
    console_timeout: float = _env_float("TERRARIA_CONSOLE_TIMEOUT", 3.0)
    #: 获取跨进程控制台锁的超时（秒）
    console_lock_timeout: float = _env_float("TERRARIA_CONSOLE_LOCK_TIMEOUT", 5.0)
    #: 控制台命令哨兵：不带参数的 kick 是无效调用（无副作用），回显固定为
    #: "Usage: kick <player>"，用它给「本次命令的回显」划一条确定性的结束边界。
    #: 注意：guard/terraria-watchd.py 必须使用同一个哨兵和同一个锁文件。
    console_sentinel: str = os.environ.get("TERRARIA_CONSOLE_SENTINEL", "kick")

    # -- 定时任务（见 docs/roadmap.md，实现于 services/scheduler.py） --------
    schedule_enabled: bool = _env_bool("SCHEDULE_ENABLED", True)
    #: 每多少分钟保存一次（0 = 关闭）
    schedule_save_minutes: int = _env_int("SCHEDULE_SAVE_MINUTES", 15)
    #: 没人在线时是否跳过保存
    schedule_save_skip_empty: bool = _env_bool("SCHEDULE_SAVE_SKIP_EMPTY", True)
    #: 每多少小时自动备份一次（0 = 关闭）
    schedule_backup_hours: float = _env_float("SCHEDULE_BACKUP_HOURS", 6)
    #: 只保留最近 N 份自动/手动备份（0 = 不清理），pre-restore 安全副本不受影响
    schedule_backup_keep: int = _env_int("SCHEDULE_BACKUP_KEEP", 10)
    #: 控制台心跳间隔（秒，0 = 关闭）：定期发一条命令，用来发现"日志停更"
    schedule_console_check_seconds: int = _env_int("SCHEDULE_CONSOLE_CHECK_SECONDS", 60)
    #: 停滞告警的冷却时间（秒），避免每分钟都发通知
    console_stall_cooldown: float = _env_float("CONSOLE_STALL_COOLDOWN", 1800.0)
    #: 服务端能读到版本、但日志超过这么多秒没有新内容 → 判定「日志停更」（issue #2）
    log_stall_seconds: float = _env_float("LOG_STALL_SECONDS", 120.0)
    #: 每天几点定时重启（"05:00"；空 = 关闭）
    schedule_restart_at: str = os.environ.get("SCHEDULE_RESTART_AT", "")
    #: 有人在线时是否跳过定时重启
    schedule_restart_skip_if_players: bool = _env_bool(
        "SCHEDULE_RESTART_SKIP_IF_PLAYERS", True
    )
    #: 重启前几分钟广播提醒（0 = 不提醒）
    schedule_restart_warn_minutes: int = _env_int("SCHEDULE_RESTART_WARN_MINUTES", 5)
    #: 定时任务用的时区（容器默认 UTC，这里显式指定，避免"凌晨 5 点"变成中午）
    schedule_timezone: str = os.environ.get("SCHEDULE_TZ", "Asia/Shanghai")

    # -- 事件通知（webhook） ---------------------------------------------
    #: 留空即关闭
    notify_webhook_url: str = os.environ.get("NOTIFY_WEBHOOK_URL", "")
    #: auto | discord | slack | json
    notify_format: str = os.environ.get("NOTIFY_FORMAT", "auto")
    #: 逗号分隔的事件白名单；空 = 全部
    notify_events: str = os.environ.get("NOTIFY_EVENTS", "")

    # -- 上传世界 --------------------------------------------------------
    #: 单个 .wld 的大小上限（字节，默认 500MB）。超限返回 413，不落盘。
    world_upload_max_bytes: int = _env_int(
        "TERRARIA_WORLD_UPLOAD_MAX_BYTES", 500 * 1024 * 1024
    )

    # -- 资源采样（issue #2） --------------------------------------------
    #: 采样间隔（秒，0 = 关闭采样）
    metrics_interval_seconds: float = _env_float("METRICS_INTERVAL_SECONDS", 60.0)
    #: 内存里保留多少个采样点（默认 1440 = 1 分钟粒度下的 24 小时）
    metrics_retention_points: int = _env_int("METRICS_RETENTION_POINTS", 1440)

    @property
    def fifo(self) -> Path:
        return self.control_dir / "command.fifo"

    @property
    def log_file(self) -> Path:
        return self.control_dir / "output.log"

    @property
    def console_lock(self) -> Path:
        return self.control_dir / "console.lock"
