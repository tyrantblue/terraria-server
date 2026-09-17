"""serverconfig.txt 读写：必须保留注释与顺序，且原子替换。"""

from __future__ import annotations

import os
from pathlib import Path

from app.services.config_service import ConfigService

ORIGINAL = """autocreate=3
world=/worlds/WSD.wld
worldname=WSD
# 这是注释，必须保留
maxplayers=255
port=7777
"""


def make(tmp_path: Path) -> ConfigService:
    path = tmp_path / "serverconfig.txt"
    path.write_text(ORIGINAL, encoding="utf-8")
    return ConfigService(path)


def test_load_skips_comments(tmp_path: Path) -> None:
    config = make(tmp_path).load()
    assert config["maxplayers"] == "255"
    assert "# 这是注释，必须保留" not in config


def test_set_replaces_in_place_and_keeps_comments(tmp_path: Path) -> None:
    service = make(tmp_path)
    service.set("world", "/worlds/gogogo.wld")

    text = service.path.read_text(encoding="utf-8")
    assert "world=/worlds/gogogo.wld" in text
    assert "# 这是注释，必须保留" in text
    assert "worldname=WSD" in text
    # 顺序不变：world 仍然在 worldname 之前
    assert text.index("world=") < text.index("worldname=")


def test_set_appends_missing_key(tmp_path: Path) -> None:
    service = make(tmp_path)
    service.set("difficulty", "1")
    assert service.path.read_text(encoding="utf-8").rstrip().endswith("difficulty=1")


def test_set_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    service = make(tmp_path)
    service.set_many({"maxplayers": "255", "port": "7777"})
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
