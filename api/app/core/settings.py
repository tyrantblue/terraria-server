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


#: 对外 API 版本。与 api/openapi.json 快照、docs/api/CHANGELOG.md 对应。
#: 破坏性变更必须升 major，并在这里改。
API_VERSION = os.environ.get("TERRARIA_API_VERSION", "1.1.0")

#: 前端构建时对应的 API 版本；后端比它高太多时前端应提示用户刷新面板。
MIN_CLIENT_VERSION = os.environ.get("TERRARIA_MIN_CLIENT_VERSION", "1.0.0")


@dataclass(frozen=True)
class Settings:
    """运行时路径与超时。字段默认值在 import 时从环境变量读取一次。"""

    worlds_dir: Path = _env_path("TERRARIA_WORLDS_DIR", "/opt/terraria/worlds")
    config_file: Path = _env_path("TERRARIA_CONFIG_FILE", "/opt/terraria/config/serverconfig.txt")
    control_dir: Path = _env_path("TERRARIA_CONTROL_DIR", "/opt/terraria/control")

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

    #: /api/server/console 默认与最大返回行数
    console_tail_default: int = _env_int("TERRARIA_CONSOLE_TAIL", 50)
    console_tail_max: int = _env_int("TERRARIA_CONSOLE_TAIL_MAX", 1000)

    @property
    def fifo(self) -> Path:
        return self.control_dir / "command.fifo"

    @property
    def log_file(self) -> Path:
        return self.control_dir / "output.log"

    @property
    def console_lock(self) -> Path:
        return self.control_dir / "console.lock"
