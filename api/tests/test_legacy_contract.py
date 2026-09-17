"""兼容性回归：所有旧接口的 URL、状态码与响应**形状**都不能变。

期望值来自 api/tests/golden/legacy_shapes.json —— 那是重构前对运行中的旧实现
逐个调用真实接口抓下来的（见 git 历史）。任何一条形状对不上，就说明这次重构
对前端造成了破坏性变更。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden" / "legacy_shapes.json"

CASES: list[tuple[str, str, dict | None]] = [
    ("GET", "/api/health", None),
    ("GET", "/api/server/status", None),
    ("GET", "/api/server/players", None),
    ("GET", "/api/server/console", None),
    ("POST", "/api/server/command", {"command": "playing"}),
    ("POST", "/api/server/playing", None),
    ("POST", "/api/server/version", None),
    ("POST", "/api/server/port", None),
    ("POST", "/api/server/maxplayers", {"max_players": 255}),
    ("POST", "/api/server/save", None),
    ("POST", "/api/server/settle", None),
    ("POST", "/api/server/time/dawn", None),
    ("POST", "/api/server/time/noon", None),
    ("POST", "/api/server/time/dusk", None),
    ("POST", "/api/server/time/midnight", None),
    ("POST", "/api/server/say", {"message": "compat-probe"}),
    ("POST", "/api/server/kick", {"player": "__nobody__"}),
    ("POST", "/api/server/ban", {"player": "__nobody__"}),
    ("POST", "/api/server/motd", {"motd": "hello"}),
    ("POST", "/api/server/password", {"password": "secret"}),
    ("GET", "/api/world/list", None),
]


def shape(value: object) -> object:
    """把响应体归一化成「结构 + 类型」，忽略具体取值。"""
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
        # 只取前两个样本，空列表单独标记
        return [shape(item) for item in value[:2]] or ["<empty>"]
    if isinstance(value, dict):
        return {key: shape(item) for key, item in sorted(value.items())}
    return type(value).__name__


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("method,path,body", CASES, ids=[f"{m} {p}" for m, p, _ in CASES])
def test_legacy_response_shape_unchanged(client, golden, method, path, body) -> None:
    key = f"{method} {path}"
    response = client.request(method, path, json=body)

    assert response.status_code == golden[key]["status"], response.text
    assert shape(response.json()) == golden[key]["shape"]


def test_world_upload_shape(client, golden) -> None:
    response = client.post(
        "/api/world/upload",
        files={"file": ("new-world.wld", b"world-bytes", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert shape(response.json()) == golden["POST /api/world/upload"]["shape"]


def test_world_switch_shape(client, golden) -> None:
    response = client.post("/api/world/switch", json={"file": "gogogo.wld"})
    assert response.status_code == 200
    assert shape(response.json()) == golden["POST /api/world/switch"]["shape"]


def test_error_body_keeps_detail_and_adds_code(client) -> None:
    """参数校验失败时保留旧的 detail 字段，同时新增机器可读的 code。"""
    response = client.post("/api/server/kick", json={"player": "   "})
    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "player cannot be empty"
    assert body["error"]["code"] == "bad_request"


def test_password_empty_is_rejected(client) -> None:
    """旧实现会放空密码过去（把服务器密码清掉），现在必须 400。"""
    response = client.post("/api/server/password", json={"password": ""})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_max_players_range_still_validated(client) -> None:
    assert client.post("/api/server/maxplayers", json={"max_players": 0}).status_code == 422
    assert client.post("/api/server/maxplayers", json={"max_players": 256}).status_code == 422


def test_meta_handshake(client) -> None:
    body = client.get("/api/meta").json()
    assert body["api_version"] == "1.4.1"
    assert body["min_client_version"] == "1.0.0"
    assert body["server_version"] == "1.4.5.8"
    assert "world.switch" in body["capabilities"]
    # 旧接口现在都会被标记弃用，并给出替代品
    deprecated = {item["path"]: item["replacement"] for item in body["deprecations"]}
    assert deprecated.get("/api/server/status") == "/api/v1/server"
    assert deprecated.get("/api/world/switch") == "/api/v1/worlds/{file}/activate"
    assert body["links"]["openapi"] == "/openapi.json"
