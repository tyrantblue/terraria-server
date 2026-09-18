"""issue #10 / #18：`POST /api/v1/scheduler/{name}/run` 要给出顶层结构化结果。

restart 类任务提交的是后台 operation，面板必须拿到**顶层** `submitted` 才能去
轮询进度，而不是立刻显示「已完成」。同时 `detail` 的中文自由文案旁边要有稳定的
`code` / `params`（issue #18）。
"""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.runtime import build_runtime
from app.services.scheduler import classify_detail


@pytest.fixture
def restart_client(settings, fake_terraria):
    """带 `restart` 定时任务的客户端（默认配置里 SCHEDULE_RESTART_AT 为空）。"""
    customized = dataclasses.replace(
        settings,
        schedule_restart_at="05:00",
        schedule_restart_warn_minutes=0,
    )
    runtime = build_runtime(customized)
    with TestClient(create_app(runtime)) as test_client:
        yield test_client, runtime


def test_restart_run_returns_top_level_submitted(restart_client) -> None:
    client, _runtime = restart_client
    response = client.post("/api/v1/scheduler/restart/run")
    assert response.status_code == 200
    body = response.json()

    assert body["code"] == "submitted"
    assert isinstance(body["submitted"], str) and body["submitted"]
    assert body["params"]["operation_id"] == body["submitted"]
    # detail 仍然保留（面向人的回退文案）
    assert body["detail"] == f"submitted: {body['submitted']}"

    # submitted 指向真实存在、可通过 /api/v1/operations 查到的 operation
    operation = client.get(f"/api/v1/operations/{body['submitted']}").json()
    assert operation["kind"] == "server.restart"


def test_scheduler_list_exposes_structured_last_detail(restart_client) -> None:
    client, _runtime = restart_client
    client.post("/api/v1/scheduler/restart/run")

    jobs = {job["name"]: job for job in client.get("/api/v1/scheduler").json()["jobs"]}
    job = jobs["restart"]
    assert job["last_code"] == "submitted"
    assert job["last_params"]["operation_id"]
    assert job["history"][0]["code"] == "submitted"
    assert job["history"][0]["params"]["operation_id"] == job["last_params"]["operation_id"]


def test_other_jobs_use_ok_code(client) -> None:
    body = client.post("/api/v1/scheduler/console/run").json()
    assert body["code"] == "ok"
    jobs = {job["name"]: job for job in client.get("/api/v1/scheduler").json()["jobs"]}
    assert jobs["console"]["last_code"] == "ok"
    assert jobs["console"]["history"][0]["code"] == "ok"


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("ok", "ok"),
        ("saved", "ok"),
        ("stalled: 控制台哨兵写入成功但 3 秒内没有回显", "stalled"),
        ("stalled (已告警过，冷却中)", "stalled"),
        ("unavailable: 控制台不可用", "unavailable.console"),
        ("error: disk full", "error"),
        ("skipped: 无人在线", "skipped.no_players"),
        ("skipped: 控制台不可用", "skipped.console_unavailable"),
        ("skipped: 3 人在线", "skipped.players_online"),
        ("submitted: abc123", "submitted"),
    ],
)
def test_classify_detail_covers_existing_prefixes(detail: str, expected: str) -> None:
    code, _params = classify_detail(detail)
    assert code == expected


def test_classify_detail_extracts_params() -> None:
    code, params = classify_detail(
        "unavailable: server.restart 正在进行（8f3c1a9b），跳过探活"
    )
    assert code == "unavailable.busy"
    assert params == {"kind": "server.restart", "operation_id": "8f3c1a9b"}

    code, params = classify_detail("submitted: deadbeef")
    assert code == "submitted"
    assert params == {"operation_id": "deadbeef"}

    code, params = classify_detail("skipped: 3 人在线")
    assert code == "skipped.players_online"
    assert params == {"online": 3}


def test_classify_detail_falls_back_to_status() -> None:
    assert classify_detail("some free text", status="succeeded")[0] == "ok"
    assert classify_detail("some free text", status="failed")[0] == "error"
    assert classify_detail("some free text", status="skipped")[0] == "skipped.other"
    assert classify_detail(None)[0] is None
