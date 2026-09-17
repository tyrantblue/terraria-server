"""备份恢复：两段式重启、安全副本、路径校验、保留策略、auto 备份。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest


def wait_operation(client, operation_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/operations/{operation_id}").json()
        if body["state"] in ("succeeded", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError("operation 未结束")


@pytest.fixture
def seeded_backup(settings, tmp_path) -> str:
    """造一份手工备份，里面的世界内容与当前世界不同。"""
    backup = settings.backup_dir / "20260101-010101"
    backup.mkdir(parents=True, exist_ok=True)
    (backup / "WSD.wld").write_bytes(b"RESTORED-WORLD-CONTENT")
    (backup / "serverconfig.txt").write_text("world=/worlds/WSD.wld\n", encoding="utf-8")
    return backup.name


def test_restore_active_world_restarts_twice(client, settings, fake_terraria, seeded_backup) -> None:
    response = client.post(f"/api/v1/backups/{seeded_backup}/restore")
    assert response.status_code == 202
    finished = wait_operation(client, response.json()["operation_id"])
    assert finished["state"] == "succeeded", finished

    # 文件真的被换成了备份里的内容
    assert (settings.worlds_dir / "WSD.wld").read_bytes() == b"RESTORED-WORLD-CONTENT"
    assert finished["result"]["active"] is True

    # 覆盖前留了安全副本
    safety = Path(finished["result"]["safety_copy"])
    assert safety.is_dir() and (safety / "WSD.wld").read_bytes() == b"W" * 128

    # 两段式：exit（加载旧世界）→ 替换 → exit-nosave（加载恢复后的世界）
    commands = fake_terraria.commands
    assert "exit" in commands and "exit-nosave" in commands
    assert commands.index("exit") < commands.index("exit-nosave")


def test_restore_inactive_world_does_not_restart(client, settings, fake_terraria) -> None:
    backup = settings.backup_dir / "20260101-020202"
    backup.mkdir(parents=True, exist_ok=True)
    (backup / "gogogo.wld").write_bytes(b"SECOND-WORLD")

    response = client.post(f"/api/v1/backups/{backup.name}/restore")
    finished = wait_operation(client, response.json()["operation_id"])
    assert finished["state"] == "succeeded", finished
    assert finished["result"]["active"] is False
    assert (settings.worlds_dir / "gogogo.wld").read_bytes() == b"SECOND-WORLD"
    assert "exit" not in fake_terraria.commands


def test_restore_from_auto_backup(client, settings) -> None:
    (settings.worlds_dir / "WSD.wld.bak").write_bytes(b"AUTO-BAK")
    response = client.post("/api/v1/backups/auto:WSD.wld.bak/restore")
    assert response.status_code == 202
    finished = wait_operation(client, response.json()["operation_id"])
    assert finished["state"] == "succeeded", finished
    assert (settings.worlds_dir / "WSD.wld").read_bytes() == b"AUTO-BAK"


def test_restore_rejects_path_traversal(client) -> None:
    assert client.post("/api/v1/backups/..%2F..%2Fetc/restore").status_code in (400, 404)
    assert client.post("/api/v1/backups/auto:..%2F..%2Fetc%2Fpasswd/restore").status_code in (400, 404)


def test_restore_unknown_backup_is_404(client) -> None:
    assert client.post("/api/v1/backups/20200101-000000/restore").status_code == 404


def test_restore_multi_world_backup_needs_file(client, settings) -> None:
    backup = settings.backup_dir / "20260101-030303"
    backup.mkdir(parents=True, exist_ok=True)
    (backup / "a.wld").write_bytes(b"a")
    (backup / "b.wld").write_bytes(b"b")
    response = client.post(f"/api/v1/backups/{backup.name}/restore")
    assert response.status_code == 400
    assert sorted(response.json()["error"]["details"]["candidates"]) == ["a.wld", "b.wld"]

    picked = client.post(f"/api/v1/backups/{backup.name}/restore", json={"file": "b.wld"})
    assert picked.status_code == 202
    finished = wait_operation(client, picked.json()["operation_id"])
    assert finished["state"] == "succeeded", finished
    assert (settings.worlds_dir / "b.wld").read_bytes() == b"b"


def test_backups_listing_includes_auto_and_manual(client, settings, seeded_backup) -> None:
    (settings.worlds_dir / "WSD.wld.bak").write_bytes(b"x")
    kinds = {item["kind"] for item in client.get("/api/v1/backups").json()["backups"]}
    assert {"manual", "auto"} <= kinds


def test_prune_keeps_pre_restore_and_legacy_dirs(settings, rt) -> None:
    for name in ("20260101-000001", "20260102-000002", "20260103-000003"):
        (settings.backup_dir / name).mkdir()
    (settings.backup_dir / "pre-restore-20260101-000000").mkdir()
    (settings.backup_dir / "config-20260914-130253").mkdir()

    result = rt.world.prune_backups(keep=1)
    assert result["removed"] == ["20260102-000002", "20260101-000001"]
    remaining = sorted(p.name for p in settings.backup_dir.iterdir())
    assert remaining == [
        "20260103-000003",
        "config-20260914-130253",
        "pre-restore-20260101-000000",
    ]


def test_prune_can_be_disabled(settings, rt) -> None:
    (settings.backup_dir / "20260101-000001").mkdir()
    assert rt.world.prune_backups(keep=0)["disabled"] is True
    assert (settings.backup_dir / "20260101-000001").is_dir()
