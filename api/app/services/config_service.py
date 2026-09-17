"""serverconfig.txt 的唯一读写入口。

旧实现的问题：只有 `load_config()` 没有写，于是 routers/world.py 自己又实现了一遍
「读全文 → 替换 world= 行 → 写回」。配置写入出现两份实现，且不是原子写。
"""

from __future__ import annotations

import os
from pathlib import Path


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
