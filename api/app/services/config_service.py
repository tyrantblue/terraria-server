"""serverconfig.txt 的唯一读写入口。

旧实现的问题：只有 `load_config()` 没有写，于是 routers/world.py 自己又实现了一遍
「读全文 → 替换 world= 行 → 写回」。配置写入出现两份实现，且不是原子写。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from app.core.errors import BadRequest

#: 允许通过 API 修改的键（白名单，避免把任意内容写进配置文件）
EDITABLE_KEYS = {
    "world",
    "worldname",
    "difficulty",
    "maxplayers",
    "port",
    "password",
    "motd",
    "autocreate",
    "seed",
    "secure",
    "upnp",
    "npcstream",
    "priority",
    "language",
}

#: 这些键只影响「运行时」，改完可以直接用控制台命令生效，不必重启
RUNTIME_KEYS = {"maxplayers", "motd", "password"}

#: 这些键必须重启服务端才会生效
RESTART_KEYS = {"world", "worldname", "difficulty", "port", "autocreate", "seed", "secure", "upnp", "language"}

_INT_RANGES = {
    "difficulty": (0, 3),
    "maxplayers": (1, 255),
    "port": (1, 65535),
    "autocreate": (1, 3),
    "secure": (0, 1),
    "upnp": (0, 1),
    "npcstream": (0, 60),
    "priority": (0, 5),
}

_TEXT_KEYS = {"worldname", "password", "motd", "seed", "language", "world"}

#: 回显时必须掩码的敏感键（见 issue #6）：面板只需要知道「改不改」，
#: 不需要知道「当前值是什么」——明文回显等于把进服密码送给任何能访问 API 的人。
SECRET_KEYS = {"password"}

#: 掩码占位符。前端看到它就知道「已设置但不可读」；
#: 它同时也是**禁止回写**的哨兵（见 validate），避免 GET 的结果被原样 PUT 回去。
SECRET_MASK = "••••••"


def secret_is_set(values: Mapping[str, object], key: str) -> bool:
    """该敏感键是否已设置（空字符串 = 没设密码）。"""
    return bool(str(values.get(key) or "").strip())


def mask_secrets(values: Mapping[str, object]) -> dict[str, str]:
    """把已设置的敏感键替换成掩码；未设置的保持原样（空字符串）。

    掩码只用于**回显**，写入路径永远拿到的是客户端提交的真实值
    （validate() 会拒绝掩码，防止把占位符当成新密码写进 serverconfig.txt）。
    """
    masked = {str(key): str(value) for key, value in values.items()}
    for key in SECRET_KEYS:
        if key in masked and secret_is_set(masked, key):
            masked[key] = SECRET_MASK
    return masked

#: maxplayers 低于这个值时提示：原版会把扫描连接也算进名额，
#: 8 个槽位很容易被「假满员」占满（见 docs/connection-guard.md）
LOW_MAX_PLAYERS = 64


def validate(values: dict[str, object]) -> dict[str, str]:
    """校验并规范化待写入的配置项，返回 {key: 字符串值}。"""
    unknown = set(values) - EDITABLE_KEYS
    if unknown:
        raise BadRequest(
            f"不支持的配置项: {', '.join(sorted(unknown))}",
            details={"editable_keys": sorted(EDITABLE_KEYS)},
        )

    normalized: dict[str, str] = {}
    for key, raw in values.items():
        if key in _INT_RANGES:
            try:
                number = int(str(raw).strip())
            except (TypeError, ValueError):
                raise BadRequest(f"{key} 必须是整数") from None
            low, high = _INT_RANGES[key]
            if not low <= number <= high:
                raise BadRequest(f"{key} 必须在 {low}..{high} 之间")
            normalized[key] = str(number)
            continue

        text = str(raw).replace("\r", "").replace("\n", " ").strip()
        if key in SECRET_KEYS and text == SECRET_MASK:
            # GET /api/v1/config 回显的就是掩码；如果前端把 values 原样 PUT 回来，
            # 掩码会变成真正的新密码。这里明确拒绝，并告诉客户端正确做法。
            raise BadRequest(
                f"{key} 收到的是掩码不是真实值：不要回显后原样提交。"
                "不修改就省略该键（或留空不发送）。",
                details={"key": key, "mask": SECRET_MASK},
            )
        if key in _TEXT_KEYS:
            # 敏感键允许空值：`password=""` 是「清空密码」（面板的「留空即移除」就靠它）。
            # 其它文本键（motd/world/seed/...）仍然不允许空。
            if not text and key not in SECRET_KEYS:
                raise BadRequest(f"{key} 不能为空")
            if len(text) > 512:
                raise BadRequest(f"{key} 太长（最多 512 字符）")
            if key == "world":
                # 只接受文件名，杜绝路径穿越；写入时统一加 /worlds/ 前缀
                name = Path(text).name
                if name != text or not name.lower().endswith(".wld"):
                    raise BadRequest("world 必须是形如 WSD.wld 的文件名")
                normalized[key] = f"/worlds/{name}"
                continue
        normalized[key] = text
    return normalized


def world_filename(value: str | None) -> str | None:
    """把配置里的 world 值（/worlds/x.wld）转换回文件名。"""
    if not value:
        return None
    return Path(value).name


def is_low_max_players(value: str | int) -> bool:
    try:
        return int(value) < LOW_MAX_PLAYERS
    except (TypeError, ValueError):
        return False


class ConfigService:
    """读写 Terraria 的 serverconfig.txt，保留注释与原有顺序。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    # -- 读 -----------------------------------------------------------
    def load(self) -> dict[str, str]:
        config: dict[str, str] = {}
        if not self.path.exists():
            return config
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            config[key.strip()] = value.strip()
        return config

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.load().get(key, default)

    # -- 写 -----------------------------------------------------------
    def set(self, key: str, value: str) -> None:
        self.set_many({key: value})

    def set_many(self, values: dict[str, str]) -> None:
        """就地替换若干键；不存在的键追加到末尾。注释与顺序保持不变。"""
        lines = (
            self.path.read_text(encoding="utf-8").splitlines()
            if self.path.exists()
            else []
        )
        remaining = dict(values)
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            matched = None
            if stripped and not stripped.startswith("#") and "=" in stripped:
                candidate = stripped.split("=", 1)[0].strip()
                if candidate in remaining:
                    matched = candidate
            if matched is None:
                out.append(line)
            else:
                out.append(f"{matched}={remaining.pop(matched)}")
        for key, value in remaining.items():
            out.append(f"{key}={value}")
        self._write_atomic("\n".join(out) + "\n")

    def _write_atomic(self, text: str) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)
