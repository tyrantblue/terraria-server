"""Terraria `.wld` 头部解析：只读文件开头的几百字节，拿到世界尺寸/难度。

为什么需要它：面板上判断「这是大世界还是小世界」「是不是专家/大师」
以前只能靠文件大小猜。世界的元数据本来就在文件头里，读几百字节就够，
不需要把 12MB 全读进来。

几个刻意的设计（见 issue #4）：

* **只用 section pointer 定位**：文件头里有「每段的字节偏移」表，
  第 0 段就是 WorldHeader。这样就不必关心 tile-frame-important 位数组
  在不同版本里的长度差异——硬编码偏移迟早会在版本升级时读错。
* **绝不抛异常**：任何解析问题（空文件、半截文件、不是 .wld、版本太老）
  都返回 `None`，接口继续返回 200，面板显示「未知」。
* **读进内存的上限**：`MAX_HEADER_BYTES`，避免被畸形文件牵着走。
* **按 (路径, 大小, mtime) 缓存**：`/api/v1/server` 每次都会列世界，
  没必要每次都重复读盘。

格式参考：Terraria 1.3.5.3（文件版本 194）以后的布局，字段与
TEdit / gopheria 的实现一致。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.settings import LOG_TIMEZONE

#: 支持的最低文件版本（1.3.5.3）。更老的布局不一样，直接返回 None。
MIN_SUPPORTED_VERSION = 194
#: 最多读进内存的字节数：WorldHeader 全在文件最前面，几 KB 绰绰有余。
MAX_HEADER_BYTES = 64 * 1024
MAGIC = b"relogic"
#: fileType：2 = 世界，4 = tModLoader 世界
WORLD_FILE_TYPES = (2, 4)

#: 官方固定尺寸 → 面板标签
_SIZE_TIERS: dict[tuple[int, int], str] = {
    (4200, 1200): "small",
    (6400, 1800): "medium",
    (8400, 2400): "large",
}
_DIFFICULTIES = {0: "classic", 1: "expert", 2: "master", 3: "journey"}

#: 尺寸兜底：非官方尺寸（mod/裁剪过的世界）按宽度归类
_LARGE_MIN_WIDTH = 8000
_MEDIUM_MIN_WIDTH = 6000

#: .NET DateTime.ToBinary：低 62 位是 ticks（100ns），高 2 位是 Kind
_TICKS_MASK = 0x3FFFFFFFFFFFFFFF
_DOTNET_EPOCH = datetime(1, 1, 1, tzinfo=timezone.utc)
#: 合理的时间范围（Terraria 2011 年发售）；超出就认为解析错了，宁可不给
_CREATED_MIN_YEAR = 2011
_CREATED_MAX_YEAR = 2100


class _ParseError(Exception):
    """头部不完整或格式不符——调用方统一降级成 metadata=None。"""


@dataclass(frozen=True)
class WorldMetadata:
    """`.wld` 头部里值得给面板看的元数据。"""

    format_version: int
    size_tier: str
    width: int
    height: int
    difficulty: str
    created_at: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "format_version": self.format_version,
            "size_tier": self.size_tier,
            "width": self.width,
            "height": self.height,
            "difficulty": self.difficulty,
            "created_at": self.created_at,
        }


class _Cursor:
    """在内存里的头部字节上顺序读取，越界一律抛 _ParseError。"""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.pos = 0

    def take(self, count: int) -> bytes:
        if count < 0 or self.pos + count > len(self._data):
            raise _ParseError("读取越界（文件被截断？）")
        chunk = self._data[self.pos : self.pos + count]
        self.pos += count
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def i16(self) -> int:
        return struct.unpack("<h", self.take(2))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def boolean(self) -> bool:
        return self.u8() != 0

    def string(self) -> str:
        """.NET 的 7-bit 长度前缀字符串（世界名与种子都这么存）。"""
        length = self._7bit_int()
        if length > 4096:
            raise _ParseError(f"字符串长度异常（{length}）")
        return self.take(length).decode("utf-8", errors="replace")

    def _7bit_int(self) -> int:
        value = 0
        shift = 0
        while True:
            byte = self.u8()
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
            if shift > 35:
                raise _ParseError("7-bit 整数过长")


def read_world_metadata(
    path: Path, *, timezone_name: str = LOG_TIMEZONE
) -> WorldMetadata | None:
    """读一个 `.wld` 的元数据；任何失败都返回 None（接口不因此失败）。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    return _cached_read(str(path), stat.st_size, stat.st_mtime, timezone_name)


def parse_world_metadata(
    data: bytes, *, timezone_name: str = LOG_TIMEZONE
) -> WorldMetadata | None:
    """解析头部字节（测试与调试用；同样的「失败即 None」语义）。"""
    try:
        return _parse(data, timezone_name=timezone_name)
    except _ParseError:
        return None
    except Exception:  # noqa: BLE001 - 畸形文件不允许打穿到 HTTP 层
        return None


@lru_cache(maxsize=64)
def _cached_read(path_str: str, size: int, mtime: float, timezone_name: str) -> WorldMetadata | None:
    """按 (路径, 大小, mtime) 缓存：世界文件被改写后会自动失效。"""
    path = Path(path_str)
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_HEADER_BYTES)
    except OSError:
        return None
    return parse_world_metadata(data, timezone_name=timezone_name)


def _parse(data: bytes, *, timezone_name: str) -> WorldMetadata:
    cursor = _Cursor(data)

    # ---- 文件头 -----------------------------------------------------
    version = cursor.i32()
    if version < MIN_SUPPORTED_VERSION:
        raise _ParseError(f"不支持的文件版本 {version}")
    if cursor.take(7) != MAGIC:
        raise _ParseError("magic 不是 relogic")
    file_type = cursor.u8()
    if file_type not in WORLD_FILE_TYPES:
        raise _ParseError(f"不是世界文件（fileType={file_type}）")

    cursor.u32()  # revision：这张图被保存过多少次
    cursor.u64()  # favorite 标记

    section_count = cursor.i16()
    if section_count < 1:
        raise _ParseError("没有 section 指针，无法定位 WorldHeader")
    pointers = [cursor.i32() for _ in range(section_count)]

    header_offset = pointers[0]
    if header_offset <= 0 or header_offset >= len(data):
        raise _ParseError(f"WorldHeader 偏移越界（{header_offset}）")
    cursor.pos = header_offset

    # ---- WorldHeader ------------------------------------------------
    cursor.string()  # 世界名（列表里已经有文件名，这里不重复暴露）
    cursor.string()  # 种子
    cursor.u64()  # 生成器版本
    cursor.take(16)  # 世界 UUID
    cursor.i32()  # 世界 ID
    for _ in range(4):  # Left / Right / Top / Bottom（世界像素范围）
        cursor.i32()

    height = cursor.i32()  # maxTilesY
    width = cursor.i32()  # maxTilesX

    difficulty = _read_difficulty(cursor, version)

    created_at = None
    if version >= 141:
        # 按无符号读：.NET 把 Kind 放在最高两位，当有符号看是负数，
        # 掩码取 ticks 时语义相同，但无符号更不容易踩坑
        created_at = _dotnet_time(cursor.u64(), timezone_name)

    _check_dimensions(width, height)

    return WorldMetadata(
        format_version=version,
        size_tier=_size_tier(width, height),
        width=width,
        height=height,
        difficulty=difficulty,
        created_at=created_at,
    )


def _read_difficulty(cursor: _Cursor, version: int) -> str:
    """难度。1.4（版本 225）起是 int32 + 一串种子开关，之前只有一个 expert 布尔。"""
    if version < 225:
        return "expert" if cursor.boolean() else "classic"

    game_mode = cursor.i32()
    cursor.boolean()  # drunk world（05162020）
    if version >= 227:
        cursor.boolean()  # for the worthy
    if version >= 238:
        cursor.boolean()  # tenth anniversary
    if version >= 239:
        cursor.boolean()  # the constant（饥荒）
    if version >= 241:
        cursor.boolean()  # not the bees
    if version >= 249:
        cursor.boolean()  # remix
    if version >= 266:
        cursor.boolean()  # no traps
    if version >= 267:
        cursor.boolean()  # zenith
    if version >= 302:
        cursor.boolean()  # skyblock
    return _DIFFICULTIES.get(game_mode, f"unknown({game_mode})")


def _check_dimensions(width: int, height: int) -> None:
    """官方世界最大 8400×2400；明显不合理的值说明读歪了，宁可不给。"""
    if not (1000 <= width <= 20000 and 500 <= height <= 8000):
        raise _ParseError(f"世界尺寸不合理：{width}×{height}")


def _size_tier(width: int, height: int) -> str:
    tier = _SIZE_TIERS.get((width, height))
    if tier:
        return tier
    if width >= _LARGE_MIN_WIDTH:
        return "large"
    if width >= _MEDIUM_MIN_WIDTH:
        return "medium"
    return "small"


def _dotnet_time(raw: int, timezone_name: str) -> str | None:
    """把 .NET `DateTime.ToBinary()` 转成 ISO-8601（UTC）。

    Kind 位是 `Local` 时，ticks 表示**创建者本地时间**：本机世界的创建者是
    服务端（用 `LOG_TIMEZONE` 解释），但玩家上传的世界保留的是对方时区，
    所以这里只能算「尽力而为」，超出合理年份就不给值。
    """
    ticks = raw & _TICKS_MASK
    kind = (raw >> 62) & 0x3
    try:
        moment = _DOTNET_EPOCH + timedelta(microseconds=ticks // 10)
    except (OverflowError, ValueError):
        return None

    if kind == 1:  # Utc
        utc = moment
    else:  # Local / Unspecified：按服务器时区解释
        try:
            utc = moment.replace(tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)
        except Exception:  # noqa: BLE001 - 时区库不可用时退化成不标注时区
            return None

    if not (_CREATED_MIN_YEAR <= utc.year <= _CREATED_MAX_YEAR):
        return None
    return utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
