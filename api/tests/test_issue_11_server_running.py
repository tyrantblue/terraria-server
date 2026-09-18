"""issue #11：`GET /api/v1/server` 的 `running` 必须反映真实探测结果。

以前 `running` 恒为 `true`，且控制台不可用 + 空缓存时直接 500，面板因此永远
显示不出「服务器已停止」，还会把「游戏服停了」误报成 "API Unreachable"。
"""

from __future__ import annotations

import time

from app.core.errors import ConsoleUnavailable

FIELDS = ("version", "port", "max_players", "time", "seed", "motd")


def test_running_true_on_the_normal_path(client) -> None:
    body = client.get("/api/v1/server").json()
    assert body["running"] is True
    assert body["version"] == "1.4.5.8"
    assert body["log_stalled"] is False


def test_console_unavailable_returns_200_not_500(client, rt, monkeypatch) -> None:
    """空缓存 + 控制台不可用：200 + running=false + 读不到的字段为 null。"""

    def boom(*_args, **_kwargs):
        raise ConsoleUnavailable("no reader on the FIFO")

    monkeypatch.setattr(rt.channel, "run", boom)

    response = client.get("/api/v1/server")
    assert response.status_code == 200
    body = response.json()
    assert body["running"] is False
    for field in FIELDS:
        assert body[field] is None, field
    assert body["players"]["online"] == 0
    # 停服期间不能因为拿不到 version 就误报日志停更
    assert body["log_stalled"] is False


def test_stopped_server_reports_offline(client, fake_terraria) -> None:
    """假服务端退出后，running 变成 false（而不是 500）。"""
    fake_terraria.stop()
    deadline = time.monotonic() + 3.0
    body = client.get("/api/v1/server").json()
    while body["running"] and time.monotonic() < deadline:
        time.sleep(0.05)
        body = client.get("/api/v1/server").json()
    assert body["running"] is False
    assert body["version"] is None
    assert body["log_stalled"] is False


def test_running_field_is_in_openapi_contract() -> None:
    from app.main import create_app

    schemas = create_app().openapi()["components"]["schemas"]
    assert "running" in schemas["ServerState"]["properties"]
    assert "running" in schemas["ServerState"]["required"]
