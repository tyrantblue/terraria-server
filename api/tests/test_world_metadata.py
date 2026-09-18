"""`.wld` 头部解析（issue #4）。

用的是**构造出来的头部**，不依赖仓库里的真实世界文件（worlds/ 不进 Git）。
布局与 TEdit / gopheria 的实现一致：文件头 → section 指针 → WorldHeader。
"""

from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

import pytest

from app.services.world_header import parse_world_metadata, read_world_metadata

#: 文件头长度 = version(4) + magic(7) + fileType(1) + revision(4)
#:              + favorite(8) + sectionCount(2) + 指针(4×1)
FILE_HEADER_SIZE = 30


def _net_string(text: str) -> bytes:
    raw = text.encode("utf-8")
    assert len(raw) < 0x80
    return bytes([len(raw)]) + raw


def _ticks(moment: datetime, *, kind: int = 1) -> int:
    """moment 的 .NET ticks；kind：0=Unspecified、1=Utc、2=Local。"""
    delta = moment - datetime(1, 1, 1)
    return (delta.days * 86400 + delta.seconds) * 10_000_000 | (kind << 62)


def _seed_flags(version: int, game_mode: int) -> bytes:
    """难度 + 各种「种子世界」开关；随版本增加，位数不对后面全读歪。"""
    if version < 225:
        return bytes([1 if game_mode else 0])
    flags = bytearray(struct.pack("<i", game_mode))
    flags += b"\x00"  # drunk world
    for limit in (227, 238, 239, 241, 249, 266, 267, 302):
        if version >= limit:
            flags += b"\x00"
    return bytes(flags)


def build_world(
    *,
    version: int = 326,
    width: int = 8400,
    height: int = 2400,
    game_mode: int = 2,
    created: int | None = None,
    magic: bytes = b"relogic",
    file_type: int = 2,
    name: str = "TestWorld",
    seed: str = "123456",
) -> bytes:
    body = _net_string(name) + _net_string(seed)
    body += struct.pack("<Q", 1)  # 生成器版本
    body += b"\x00" * 16  # 世界 UUID
    body += struct.pack("<i", 42)  # 世界 ID
    body += struct.pack("<iiii", 0, width * 16, 0, height * 16)  # Left/Right/Top/Bottom
    body += struct.pack("<ii", height, width)
    body += _seed_flags(version, game_mode)
    body += struct.pack("<Q", _ticks(datetime(2026, 9, 1, 12, 0, 0)) if created is None else created)

    head = (
        struct.pack("<i", version)
        + magic
        + struct.pack("<B", file_type)
        + struct.pack("<I", 7)  # revision
        + struct.pack("<Q", 0)  # favorite
        + struct.pack("<h", 1)  # section 数量
        + struct.pack("<i", FILE_HEADER_SIZE)  # 指针[0] → WorldHeader
    )
    return head + body


# ---------------------------------------------------------------- 解析
@pytest.mark.parametrize(
    "width,height,tier",
    [(4200, 1200, "small"), (6400, 1800, "medium"), (8400, 2400, "large")],
)
def test_size_tiers(width: int, height: int, tier: str) -> None:
    metadata = parse_world_metadata(build_world(width=width, height=height))
    assert metadata is not None
    assert (metadata.width, metadata.height) == (width, height)
    assert metadata.size_tier == tier


def test_metadata_fields() -> None:
    metadata = parse_world_metadata(build_world(version=326, game_mode=1))
    assert metadata is not None
    assert metadata.format_version == 326
    assert metadata.difficulty == "expert"


@pytest.mark.parametrize(
    "game_mode,difficulty",
    [(0, "classic"), (1, "expert"), (2, "master"), (3, "journey")],
)
def test_difficulty(game_mode: int, difficulty: str) -> None:
    metadata = parse_world_metadata(build_world(game_mode=game_mode))
    assert metadata is not None
    assert metadata.difficulty == difficulty


def test_created_at_utc_kind() -> None:
    metadata = parse_world_metadata(build_world(created=_ticks(datetime(2026, 9, 1, 12, 0, 0))))
    assert metadata is not None
    assert metadata.created_at == "2026-09-01T12:00:00Z"


def test_created_at_local_kind_uses_server_timezone() -> None:
    raw = _ticks(datetime(2026, 9, 1, 12, 0, 0), kind=2)  # Kind = Local
    metadata = parse_world_metadata(build_world(created=raw), timezone_name="Asia/Shanghai")
    assert metadata is not None
    assert metadata.created_at == "2026-09-01T04:00:00Z"


def test_absurd_created_at_is_dropped() -> None:
    """时间明显不合理（例如读歪了）就给 null，而不是把错误数据端给面板。"""
    metadata = parse_world_metadata(build_world(created=_ticks(datetime(1900, 1, 1))))
    assert metadata is not None
    assert metadata.created_at is None


# ---------------------------------------------------------------- 优雅降级
@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"W" * 128,  # 测试里大量存在的占位文件
        b"\x00" * 512,
        build_world(magic=b"notrelog")[:200],
        build_world(file_type=3),  # 玩家文件，不是世界
        build_world(version=100),  # 太老，布局不同
        build_world()[:60],  # 被截断
    ],
)
def test_broken_files_return_none(data: bytes) -> None:
    assert parse_world_metadata(data) is None


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_world_metadata(tmp_path / "nope.wld") is None


def test_section_pointer_out_of_range_returns_none() -> None:
    data = bytearray(build_world())
    struct.pack_into("<i", data, 26, 10**9)  # 指针[0] 指向文件外
    assert parse_world_metadata(bytes(data)) is None


# ---------------------------------------------------------------- 接口
def test_worlds_list_exposes_metadata(client, settings) -> None:
    (settings.worlds_dir / "built.wld").write_bytes(build_world())
    worlds = {item["file"]: item for item in client.get("/api/v1/worlds").json()["worlds"]}
    assert worlds["built.wld"]["metadata"] == {
        "format_version": 326,
        "size_tier": "large",
        "width": 8400,
        "height": 2400,
        "difficulty": "master",
        "created_at": "2026-09-01T12:00:00Z",
    }


def test_corrupt_world_still_returns_200(client) -> None:
    """conftest 里的 WSD.wld 就是 128 字节的垃圾数据：接口必须照常工作。"""
    response = client.get("/api/v1/worlds")
    assert response.status_code == 200
    worlds = {item["file"]: item for item in response.json()["worlds"]}
    assert worlds["WSD.wld"]["metadata"] is None
    assert worlds["WSD.wld"]["size"] == 128


def test_server_state_world_also_has_metadata(client, settings) -> None:
    (settings.worlds_dir / "built.wld").write_bytes(build_world(width=4200, height=1200))
    settings.config_file.write_text("world=/worlds/built.wld\n", encoding="utf-8")
    body = client.get("/api/v1/server").json()
    assert body["world"]["file"] == "built.wld"
    assert body["world"]["metadata"]["size_tier"] == "small"
