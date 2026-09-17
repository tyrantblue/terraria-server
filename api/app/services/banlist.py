"""封禁名单（banlist.txt）。

原版控制台只有 `ban <player>`，**没有 unban**，而且只会给「当前在线」的玩家写名单；
文件位置默认是存档目录下的 banlist.txt（本项目即 /worlds/banlist.txt），
在第一次封禁之前根本不存在。

因此这里的设计是「防御式」的：
* 读：文件不存在就返回空列表，不猜测格式之外的任何东西；
* 删：按行匹配移除，其余内容原样保留；不改变文件其它部分。

在线踢/封仍然走控制台命令（见 ServerService），这里只负责名单本身。
"""

from __future__ import annotations

import os
from pathlib import Path


class BanList:
    def __init__(self, worlds_dir: Path, config_path: Path | None = None) -> None:
        self.worlds_dir = worlds_dir
        self.config_path = config_path

    # -- 路径 ---------------------------------------------------------
    @property
    def path(self) -> Path:
        """优先用 serverconfig.txt 里的 banlist= 配置，否则用存档目录下的默认位置。"""
        if self.config_path and self.config_path.exists():
            for line in self.config_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("banlist=") and not stripped.startswith("#"):
                    value = stripped.split("=", 1)[1].strip()
                    if value:
                        candidate = Path(value)
                        if not candidate.is_absolute():
                            candidate = self.worlds_dir / candidate
                        return candidate
        return self.worlds_dir / "banlist.txt"

    # -- 读 -----------------------------------------------------------
    def entries(self) -> list[str]:
        path = self.path
        if not path.is_file():
            return []
        names: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            name = line.strip()
            if name and not name.startswith("#"):
                names.append(name)
        return names

    def exists(self) -> bool:
        return self.path.is_file()

    # -- 写 -----------------------------------------------------------
    def remove(self, name: str) -> bool:
        """按行移除一个名字。返回是否真的改动了文件。"""
        path = self.path
        if not path.is_file():
            return False
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        kept = [line for line in lines if line.strip() != name.strip()]
        if len(kept) == len(lines):
            return False
        self._write_atomic("\n".join(kept) + ("\n" if kept else ""))
        return True

    def _write_atomic(self, text: str) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)
