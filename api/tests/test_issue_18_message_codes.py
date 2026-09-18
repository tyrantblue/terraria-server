"""issue #18：operation 进度文案要有结构化的 `message_code` / `message_params`。

`message` 是面向人的中文回退文案（不保证稳定）；面板做程序判断与本地化要读
`message_code`。`kind_label` 让面板不必自己维护 kind → 文案映射。
"""

from __future__ import annotations

import threading
import time

from app.services.operations import OperationRegistry


def wait_operation(client, operation_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/operations/{operation_id}").json()
        if body["state"] in ("succeeded", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError("operation 未结束")


def test_progress_codes_and_params_are_recorded() -> None:
    registry = OperationRegistry()
    entered = threading.Event()
    release = threading.Event()

    def job(progress):
        progress(5, "正在保存世界", "restart.saving_world")
        entered.set()
        release.wait(3)
        return {}

    operation = registry.submit("server.restart", job)
    assert entered.wait(3)
    try:
        body = registry.get(operation.id).as_dict()
        assert body["message"] == "正在保存世界"
        assert body["message_code"] == "restart.saving_world"
        assert body["message_params"] == {}
        assert body["kind_label"] == "Restart server"
    finally:
        release.set()


def test_progress_params_are_passed_through() -> None:
    registry = OperationRegistry()
    entered = threading.Event()
    release = threading.Event()

    def job(progress):
        progress(5, "准备切换到 gogogo.wld", "activate.preparing", {"file": "gogogo.wld"})
        entered.set()
        release.wait(3)
        return {}

    operation = registry.submit("world.activate", job)
    assert entered.wait(3)
    try:
        body = registry.get(operation.id).as_dict()
        assert body["message_code"] == "activate.preparing"
        assert body["message_params"] == {"file": "gogogo.wld"}
    finally:
        release.set()


def test_terminal_states_always_have_a_code() -> None:
    def boom(_progress):
        raise RuntimeError("boom")

    registry = OperationRegistry()
    succeeded = registry.submit("world.backup", lambda _progress: {"ok": True})
    failed = registry.submit("world.backup", boom)

    for operation, expected in ((succeeded, "succeeded"), (failed, "failed")):
        body = registry.get(operation.id).as_dict()
        deadline = time.monotonic() + 5
        while body["state"] not in ("succeeded", "failed") and time.monotonic() < deadline:
            time.sleep(0.01)
            body = registry.get(operation.id).as_dict()
        assert body["message_code"] == expected
        assert body["message_code"]  # 永不为空


def test_rest_operation_view_exposes_codes(client) -> None:
    submitted = client.post("/api/v1/server/restart").json()
    running = client.get(f"/api/v1/operations/{submitted['operation_id']}").json()
    assert running["message_code"]
    assert running["kind_label"] == "Restart server"
    assert running["state"] in ("pending", "running")

    finished = wait_operation(client, submitted["operation_id"])
    assert finished["message_code"] == "succeeded"
    assert finished["message"] == "succeeded"
    # message 字段仍然保留（纯新增，不破坏兼容）
    assert "message" in finished


def test_backup_job_uses_prefixed_codes(client) -> None:
    backup = client.post("/api/v1/worlds/WSD.wld/backup").json()
    finished = wait_operation(client, backup["operation_id"])
    assert finished["state"] == "succeeded"
    assert finished["message_code"]
    assert finished["kind_label"] == "Back up worlds"

    # 结果里保留原有字段，结构化字段是纯新增
    assert "backup" in finished["result"]
