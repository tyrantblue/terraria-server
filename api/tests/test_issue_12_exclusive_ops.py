"""issue #12：`world.restore` 必须进互斥集合，备份不能与恢复并发。

恢复会「停服 → 覆盖世界文件 → 再启动」，与重启/切世界属于同一类；备份会读
正在被覆盖的世界文件，可能拿到半成品。
"""

from __future__ import annotations

import time

import pytest

from app.services.operations import CONFLICTS_WITH, EXCLUSIVE_KINDS


def wait_operation(client, operation_id: str, timeout: float = 25.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/operations/{operation_id}").json()
        if body["state"] in ("succeeded", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError("operation 未结束")


@pytest.fixture
def backup_name(settings) -> str:
    backup = settings.backup_dir / "20260101-010101"
    backup.mkdir(parents=True, exist_ok=True)
    (backup / "WSD.wld").write_bytes(b"RESTORED")
    return backup.name


def test_exclusive_kinds_include_restore_and_drop_config_apply() -> None:
    assert EXCLUSIVE_KINDS == {"server.restart", "world.activate", "world.restore"}
    assert "config.apply" not in EXCLUSIVE_KINDS
    assert CONFLICTS_WITH["world.backup"] == {"world.restore"}


def test_restore_blocks_restart(client, backup_name) -> None:
    first = client.post(f"/api/v1/backups/{backup_name}/restore")
    assert first.status_code == 202

    blocked = client.post("/api/v1/server/restart")
    assert blocked.status_code == 409
    details = blocked.json()["error"]["details"]
    assert details["kind"] == "world.restore"
    assert details["operation_id"] == first.json()["operation_id"]

    assert wait_operation(client, first.json()["operation_id"])["state"] == "succeeded"


def test_restore_blocks_world_activate(client, backup_name) -> None:
    first = client.post(f"/api/v1/backups/{backup_name}/restore")
    assert first.status_code == 202

    blocked = client.post("/api/v1/worlds/gogogo.wld/activate")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["kind"] == "world.restore"

    wait_operation(client, first.json()["operation_id"])


def test_only_one_concurrent_restore(client, backup_name) -> None:
    first = client.post(f"/api/v1/backups/{backup_name}/restore")
    assert first.status_code == 202

    second = client.post(f"/api/v1/backups/{backup_name}/restore")
    assert second.status_code == 409
    assert second.json()["error"]["details"]["operation_id"] == first.json()["operation_id"]

    wait_operation(client, first.json()["operation_id"])


def test_backup_is_rejected_while_restoring(client, backup_name) -> None:
    first = client.post(f"/api/v1/backups/{backup_name}/restore")
    assert first.status_code == 202

    blocked = client.post("/api/v1/worlds/WSD.wld/backup")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"]["kind"] == "world.restore"

    wait_operation(client, first.json()["operation_id"])
