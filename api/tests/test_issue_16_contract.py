"""issue #16：四处字段/错误码与文档不一致的收敛清单。

1. 上传同名世界的 409 缺 `details.file`
2. `OperationRef.poll` 是未替换的模板字符串
3. pydantic 校验失败（422）不走统一错误信封
4. 守卫非法 IP 用 503（见 test_guard_api.py 的用例）
"""

from __future__ import annotations

import time

import pytest


def wait_operation(client, operation_id: str, timeout: float = 25.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/operations/{operation_id}").json()
        if body["state"] in ("succeeded", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError("operation 未结束")


# ---------------------------------------------------------------- 1. 409 details.file
def test_duplicate_upload_conflict_carries_file(client) -> None:
    response = client.post(
        "/api/v1/worlds",
        files={"file": ("WSD.wld", b"duplicate", "application/octet-stream")},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["details"]["file"] == "WSD.wld"


# ---------------------------------------------------------------- 2. poll 真实 URL
def test_restart_poll_is_a_real_url(client) -> None:
    response = client.post("/api/v1/server/restart")
    assert response.status_code == 202
    body = response.json()
    assert "{operation_id}" not in response.text
    assert body["poll"] == f"/api/v1/operations/{body['operation_id']}"
    assert client.get(body["poll"]).status_code == 200
    wait_operation(client, body["operation_id"])


def test_world_operation_polls_are_real_urls(client) -> None:
    activated = client.post("/api/v1/worlds/gogogo.wld/activate").json()
    assert activated["poll"] == f"/api/v1/operations/{activated['operation_id']}"
    wait_operation(client, activated["operation_id"])

    backup = client.post("/api/v1/worlds/WSD.wld/backup").json()
    assert backup["poll"] == f"/api/v1/operations/{backup['operation_id']}"
    wait_operation(client, backup["operation_id"])


def test_operation_ref_schema_has_no_template_default() -> None:
    from app.main import create_app

    schemas = create_app().openapi()["components"]["schemas"]
    poll = schemas["OperationRef"]["properties"]["poll"]
    assert "{operation_id}" not in str(poll)


# ---------------------------------------------------------------- 3. 422 统一信封
def test_validation_error_uses_unified_envelope(client) -> None:
    response = client.post("/api/v1/server/time", json={"phase": "teatime"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    # 保留原错误数组，旧客户端（formatValidationErrors）继续可用
    assert isinstance(body["detail"], list) and body["detail"]


def test_body_validation_error_also_uses_envelope(client) -> None:
    response = client.post("/api/v1/server/actions", json={"action": "nope"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------- 4. 守卫 400/503
def test_guard_input_error_is_400_and_outage_is_503(client) -> None:
    bad = client.post("/api/v1/guard/allow", json={"ip": "::1"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "bad_request"

    # 守卫不在（没有控制文件）→ 503
    down = client.post("/api/v1/guard/allow", json={"ip": "198.51.100.44"})
    assert down.status_code == 503
    assert down.json()["error"]["code"] == "guard_unavailable"
