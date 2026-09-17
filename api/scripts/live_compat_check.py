#!/usr/bin/env python3
"""线上契约比对（默认只读）。

用途：部署后确认旧接口的**状态码与响应形状**没有变。

⚠️ 血泪教训：不要拿会改状态的接口去「验证线上」。曾经用
`POST /api/server/motd {"motd": "m"}` 探测，把线上 MOTD 改成了 "m"。
所以这个脚本默认**跳过所有会写数据的接口**，它们由 tests/test_legacy_contract.py
在假 Terraria 上覆盖。

用法：
    uv run python scripts/live_compat_check.py                 # 只读比对（默认）
    uv run python scripts/live_compat_check.py --include-writes # 明确知道后果再用
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "legacy_shapes.json"
BASE = "http://localhost:8080"

#: 会改动服务端/文件状态的接口，默认不碰
MUTATING = {
    "POST /api/server/say",
    "POST /api/server/kick",
    "POST /api/server/ban",
    "POST /api/server/motd",
    "POST /api/server/password",
    "POST /api/server/maxplayers",
    "POST /api/server/save",
    "POST /api/server/settle",
    "POST /api/server/time/dawn",
    "POST /api/server/time/noon",
    "POST /api/server/time/dusk",
    "POST /api/server/time/midnight",
    "POST /api/world/upload",
    "POST /api/world/switch",
}

#: 需要请求体的只读接口
BODIES = {"POST /api/server/command": {"command": "playing"}}

SAFE_VALUES = {
    "POST /api/server/say": {"message": "compat-probe"},
    "POST /api/server/kick": {"player": "__nobody__"},
    "POST /api/server/ban": {"player": "__nobody__"},
    "POST /api/server/motd": {"motd": "__compat_probe__"},
    "POST /api/server/password": {"password": "123456"},
    "POST /api/server/maxplayers": {"max_players": 255},
}


def shape(value: object) -> object:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return [shape(item) for item in value[:2]] or ["<empty>"]
    if isinstance(value, dict):
        return {key: shape(item) for key, item in sorted(value.items())}
    return type(value).__name__


def call(method: str, path: str, body: dict | None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001
            return exc.code, None


def main(argv: list[str]) -> int:
    include_writes = "--include-writes" in argv
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    checked = skipped = failed = 0
    for key, expected in sorted(golden.items()):
        if "note" in expected and "world" in key:
            continue  # upload/switch 只记录了形状，不实际调用
        if key in MUTATING and not include_writes:
            skipped += 1
            continue

        method, path = key.split(" ", 1)
        body = BODIES.get(key) or (SAFE_VALUES.get(key) if include_writes else None)
        status, payload = call(method, path, body)
        actual = shape(payload)
        if status != expected["status"] or actual != expected["shape"]:
            failed += 1
            print(f"MISMATCH {key}")
            print(f"   期望 {expected['status']} {expected['shape']}")
            print(f"   实际 {status} {actual}")
        else:
            checked += 1
            print(f"OK  {key}")

    print()
    print(f"只读比对 {checked} 个，跳过写接口 {skipped} 个，失败 {failed} 个")
    if skipped and not include_writes:
        print("（写接口的响应形状由 pytest 在假 Terraria 上验证）")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
