#!/usr/bin/env python3
"""导出 OpenAPI 快照。

契约同步的三件套之一（另外两件是 docs/api/CHANGELOG.md 和 GET /api/meta）：
把 schema 快照提交进仓库，前端据此生成 TypeScript 类型；CI 用 --check 保证
「接口改了但快照没更新」会被拦住。

用法（在 api/ 目录下）：
    python scripts/export_openapi.py            # 写入 api/openapi.json
    python scripts/export_openapi.py --check     # 只校验，不一致则退出码 1
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402

SNAPSHOT = ROOT / "openapi.json"


def render() -> str:
    spec = create_app().openapi()
    return json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if not SNAPSHOT.exists():
            print(f"缺少快照文件 {SNAPSHOT}，请先运行 python scripts/export_openapi.py")
            return 1
        if SNAPSHOT.read_text(encoding="utf-8") != text:
            print("openapi.json 与当前代码不一致：接口契约变了，请更新快照。")
            print("确认这是有意的改动后运行： python scripts/export_openapi.py")
            return 1
        print("openapi.json 与代码一致。")
        return 0

    SNAPSHOT.write_text(text, encoding="utf-8")
    print(f"已写入 {SNAPSHOT}（{len(text)} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
