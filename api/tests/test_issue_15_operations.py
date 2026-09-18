"""issue #15：operation 404 用专用错误码，并明确保留策略。

面板把「API 重启导致 operation 丢失」当成「结果未知」，不能靠 message 正则
判断；后端改一句文案就会失效。
"""

from __future__ import annotations

from app.services.operations import OperationRegistry


def test_unknown_operation_uses_dedicated_code(client) -> None:
    response = client.get("/api/v1/operations/deadbeef")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "operation_not_found"
    assert body["error"]["details"]["operation_id"] == "deadbeef"


def test_missing_route_still_uses_generic_not_found(client) -> None:
    response = client.get("/api/v1/definitely-not-a-route")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_operations_list_exposes_in_flight_and_state_filter(client) -> None:
    import time

    empty = client.get("/api/v1/operations").json()
    assert empty["in_flight"] == 0

    submitted = client.post("/api/v1/server/restart").json()
    in_flight = client.get("/api/v1/operations").json()["in_flight"]
    assert in_flight >= 1

    succeeded = client.get("/api/v1/operations", params={"state": "succeeded"}).json()
    assert all(op["state"] == "succeeded" for op in succeeded["operations"])
    assert all(
        op["id"] != submitted["operation_id"] for op in succeeded["operations"]
    )

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if client.get(f"/api/v1/operations/{submitted['operation_id']}").json()[
            "state"
        ] in ("succeeded", "failed"):
            break
        time.sleep(0.1)


def test_registry_keeps_only_the_configured_history() -> None:
    """最多保留 N 条、新的在前——这是 issue #15 要求写进文档的保留策略。"""
    registry = OperationRegistry(max_history=3)
    ids = [registry.submit("world.backup", lambda _progress: None).id for _ in range(5)]
    listed = [operation.id for operation in registry.list()]
    assert listed == list(reversed(ids[-3:]))


def test_registry_in_flight_counts_unfinished() -> None:
    import threading

    registry = OperationRegistry()
    release = threading.Event()

    def blocking(_progress):
        release.wait(3)
        return {}

    operation = registry.submit("world.backup", blocking)
    try:
        assert registry.in_flight() == 1
    finally:
        release.set()
        for _ in range(100):
            if registry.get(operation.id).state in ("succeeded", "failed"):
                break
            threading.Event().wait(0.01)
    assert registry.in_flight() == 0
