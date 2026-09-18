"""契约快照测试：接口变了但 api/openapi.json 没更新 → 失败。

这是「前端契约同步」在 CI 里的那道闸门（见 docs/api-refactor-plan.md §5.1）。
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
    return json.dumps(create_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_snapshot_exists() -> None:
    assert SNAPSHOT.exists(), "缺少 api/openapi.json，请运行 python scripts/export_openapi.py"


def test_openapi_matches_snapshot() -> None:
    assert SNAPSHOT.read_text(encoding="utf-8") == render(), (
        "接口契约与快照不一致：确认改动是有意的，然后运行 "
        "python scripts/export_openapi.py 并在 docs/api/CHANGELOG.md 里记录"
    )


def test_legacy_paths_are_gone() -> None:
    """2.0.0 删掉了旧 `/api/*`：它们必须从契约里彻底消失，别被误加回来。"""
    spec = json.loads(render())
    paths = spec["paths"]
    for path in (
        "/api/server/status",
        "/api/server/console",
        "/api/server/command",
        "/api/world/list",
        "/api/world/upload",
        "/api/world/switch",
        "/api/meta/usage",
    ):
        assert path not in paths, f"{path} 应当在 2.0.0 被删除"
    for path in ("/api/health", "/api/meta", "/api/v1/server"):
        assert path in paths, f"{path} 不该被删掉"


def test_responses_have_schemas() -> None:
    """P0 的核心收益之一：响应不再是空的 {}，前端能生成类型。"""
    spec = json.loads(render())
    status = spec["paths"]["/api/v1/server"]["get"]["responses"]["200"]
    schema = status["content"]["application/json"]["schema"]
    assert schema, "/api/v1/server 的 200 响应没有 schema"
