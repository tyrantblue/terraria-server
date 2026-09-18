"""审计落盘（issue #3 第 1 部分）与上传健壮性（第 2 部分）。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import world_service
from app.services.console.audit import AuditLog
from app.services.runtime import build_runtime
from app.services.world_service import (
    InsufficientStorage,
    PayloadTooLarge,
    precheck_upload,
    required_free_bytes,
)


# ---------------------------------------------------------------- 审计落盘
def test_audit_entries_are_written_to_disk(rt) -> None:
    rt.audit.record("save", actor="api", result="Saved world.")
    audit_file = rt.settings.control_dir / "audit.log"
    assert audit_file.exists()

    payload = json.loads(audit_file.read_text(encoding="utf-8").strip())
    assert payload["command"] == "save"
    assert payload["actor"] == "api"
    assert payload["result"] == "Saved world."
    assert payload["ts"] > 0


def test_audit_survives_process_restart(rt) -> None:
    """审计的价值就在于 API 重启后还在——用新的 AuditLog 实例模拟重启。"""
    rt.audit.record("say hello", actor="api", result="ok")
    restarted = AuditLog(rt.settings.control_dir / "audit.log")
    assert restarted.entries() == []  # 内存缓存是空的
    tail = restarted.entries(tail=10)
    assert [entry.command for entry in tail] == ["say hello"]


def test_audit_tail_reads_from_file(client, settings) -> None:
    client.post("/api/v1/console/commands", json={"command": "seed"})
    body = client.get("/api/v1/console/audit", params={"tail": 5}).json()
    assert body["entries"][0]["command"] == "seed"
    assert body["entries"][0]["actor"] == "api"
    assert body["entries"][0]["result"] is not None
    assert (settings.control_dir / "audit.log").exists()


def test_audit_result_is_truncated(rt) -> None:
    rt.audit.record("playing", actor="api", result="x" * 5000)
    assert len(rt.audit.entries()[0].result) < 600


def test_audit_failure_does_not_break_the_command(tmp_path: Path) -> None:
    """审计写不进去（磁盘满/目录只读）时命令仍要成功，只记 warning。

    这里把「文件」指向一个目录，open("a") 必然抛 OSError，不需要改内部实现。
    """
    broken = AuditLog(tmp_path)
    entry = broken.record("save", actor="api", result="ok")
    assert entry.command == "save"
    assert broken.entries()[0].command == "save"


def test_audit_tail_falls_back_to_memory_newest_first(tmp_path: Path) -> None:
    """回读不到文件时（落盘失败/权限不足）`?tail=N` 仍要返回**最近** N 条。"""
    broken = AuditLog(tmp_path)          # 路径是目录 → _read_file() 永远为空
    for index in range(5):
        broken.record(f"cmd{index}", actor="api")
    assert [entry.command for entry in broken.entries(tail=2)] == ["cmd4", "cmd3"]


# ---------------------------------------------------------------- 预检
def test_precheck_rejects_oversize(tmp_path: Path) -> None:
    with pytest.raises(PayloadTooLarge) as excinfo:
        precheck_upload(tmp_path, declared_size=2_000, max_bytes=1_000)
    assert excinfo.value.status_code == 413
    assert excinfo.value.details["limit_bytes"] == 1_000


def test_precheck_rejects_low_disk(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(world_service, "free_bytes", lambda _path: 1024)
    with pytest.raises(InsufficientStorage) as excinfo:
        precheck_upload(tmp_path, declared_size=10 * 1024 * 1024, max_bytes=100 * 1024 * 1024)
    assert excinfo.value.status_code == 507


def test_required_free_bytes_keeps_a_floor() -> None:
    assert required_free_bytes(0) == world_service.MIN_FREE_BYTES
    assert required_free_bytes(100 * 1024 * 1024) == 200 * 1024 * 1024


# ---------------------------------------------------------------- 上传
@pytest.fixture
def small_limit_client(settings, fake_terraria):
    """把上限调到 1KB 的客户端，用来测 413 而不必真传 500MB。"""
    tiny = dataclasses.replace(settings, world_upload_max_bytes=1024)
    runtime = build_runtime(tiny)
    with TestClient(create_app(runtime)) as test_client:
        yield test_client


def test_upload_over_limit_is_413_without_leftovers(small_limit_client, settings) -> None:
    response = small_limit_client.post(
        "/api/v1/worlds",
        files={"file": ("big.wld", b"W" * 4096, "application/octet-stream")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert not (settings.worlds_dir / "big.wld").exists()
    assert not (settings.worlds_dir / "big.wld.part").exists()


class _FakeStream:
    """按块返回字节的假上传流（没有 Content-Length 的 chunked 请求）。"""

    def __init__(self, data: bytes, chunk: int | None = None) -> None:
        self._data = data
        self._pos = 0
        self._chunk = chunk

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._data)
        if self._chunk:
            size = min(size, self._chunk)
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


class _SlowStream(_FakeStream):
    """每个 chunk 之间让出事件循环，用来制造真正的并发交错。"""

    async def read(self, size: int = -1) -> bytes:
        await asyncio.sleep(0)
        return await super().read(size)


def test_streaming_limit_refuses_without_content_length(rt) -> None:
    """没有 Content-Length 时中间件不会拦，靠写入过程的累计上限兜底。"""
    rt.world.max_upload_bytes = 512
    with pytest.raises(PayloadTooLarge):
        asyncio.run(
            rt.world.upload("streamed.wld", _FakeStream(b"W" * 4096), declared_size=None)
        )
    assert not (rt.settings.worlds_dir / "streamed.wld").exists()
    assert not (rt.settings.worlds_dir / "streamed.wld.part").exists()


def test_upload_low_disk_is_507(client, monkeypatch) -> None:
    monkeypatch.setattr(world_service, "free_bytes", lambda _path: 4096)
    response = client.post(
        "/api/v1/worlds",
        files={"file": ("full.wld", b"W" * 1024, "application/octet-stream")},
    )
    assert response.status_code == 507
    assert response.json()["error"]["code"] == "insufficient_storage"
    assert [item["file"] for item in client.get("/api/v1/worlds").json()["worlds"]] == [
        "WSD.wld",
        "gogogo.wld",
    ]


def test_upload_is_atomic_and_cleans_part_file(client, settings) -> None:
    created = client.post(
        "/api/v1/worlds",
        files={"file": ("atomic.wld", b"world-bytes", "application/octet-stream")},
    )
    assert created.status_code == 201
    assert (settings.worlds_dir / "atomic.wld").read_bytes() == b"world-bytes"
    assert list(settings.worlds_dir.glob("*.part")) == []


def test_concurrent_same_name_uploads_do_not_collide(rt) -> None:
    """同名并发上传各自写自己的 .part：结果必须是其中一份完整内容，不能互相写坏。"""
    first = b"A" * 4096
    second = b"B" * 4096

    async def run() -> None:
        await asyncio.gather(
            rt.world.upload("same.wld", _SlowStream(first, chunk=256)),
            rt.world.upload("same.wld", _SlowStream(second, chunk=256)),
        )

    asyncio.run(run())
    written = (rt.settings.worlds_dir / "same.wld").read_bytes()
    assert written in (first, second)
    assert list(rt.settings.worlds_dir.glob("*.part")) == []


def test_empty_upload_is_rejected(client) -> None:
    response = client.post(
        "/api/v1/worlds",
        files={"file": ("empty.wld", b"", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
