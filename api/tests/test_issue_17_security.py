"""issue #17：写操作限流、客户端版本门槛、可选 token、banlist 并发。"""

from __future__ import annotations

import dataclasses
import threading
import time

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.banlist import BanList
from app.services.runtime import build_runtime


def _secured_client(settings, **overrides):
    customized = dataclasses.replace(settings, **overrides)
    return TestClient(create_app(build_runtime(customized)))


def _wait_operation(client, operation_id: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/api/v1/operations/{operation_id}").json()["state"]
        if state in ("succeeded", "failed"):
            return
        time.sleep(0.1)


# ---------------------------------------------------------------- 2. 限流
def test_write_rate_limit_returns_429(settings, fake_terraria) -> None:
    with _secured_client(settings, rate_limit_write_per_minute=3) as client:
        for _ in range(3):
            response = client.post("/api/v1/server/actions", json={"action": "save"})
            assert response.status_code == 200

        blocked = client.post("/api/v1/server/actions", json={"action": "save"})
        assert blocked.status_code == 429
        body = blocked.json()
        assert body["error"]["code"] == "too_many_requests"
        assert body["error"]["details"]["bucket"] == "write"
        assert blocked.headers["retry-after"]


def test_restart_rate_limit_is_tighter(settings, fake_terraria) -> None:
    with _secured_client(
        settings, rate_limit_restart_per_minute=1, rate_limit_write_per_minute=100
    ) as client:
        first = client.post("/api/v1/server/restart")
        assert first.status_code == 202
        second = client.post("/api/v1/server/restart")
        assert second.status_code == 429
        assert second.json()["error"]["details"]["bucket"] == "restart"
        _wait_operation(client, first.json()["operation_id"])


def test_rate_limit_can_be_disabled(settings, fake_terraria) -> None:
    with _secured_client(
        settings, rate_limit_enabled=False, rate_limit_write_per_minute=1
    ) as client:
        for _ in range(3):
            assert (
                client.post("/api/v1/server/actions", json={"action": "save"}).status_code
                == 200
            )


# ---------------------------------------------------------------- 3. 客户端版本门槛
def test_outdated_client_is_rejected(client) -> None:
    stale = client.get("/api/v1/server", headers={"X-Client-Version": "1.0.0"})
    assert stale.status_code == 426
    body = stale.json()
    assert body["error"]["code"] == "client_outdated"
    assert body["error"]["details"]["min_client_version"] == "1.4.1"
    # 早退响应也要带回显头
    assert stale.headers["x-client-version"] == "1.0.0"


def test_client_version_gate_exemptions(client) -> None:
    # 不带头的调用方（脚本/curl）不受影响
    assert client.get("/api/v1/server").status_code == 200
    # 达到门槛放行
    assert (
        client.get("/api/v1/server", headers={"X-Client-Version": "1.4.1"}).status_code
        == 200
    )
    # 握手接口永远放行，老面板才能知道自己该升级
    assert (
        client.get("/api/meta", headers={"X-Client-Version": "0.1.0"}).status_code == 200
    )
    # 非版本字符串（自定义 build 标记）不拦：这不是安全边界
    assert (
        client.get("/api/v1/server", headers={"X-Client-Version": "dev"}).status_code
        == 200
    )


def test_client_version_is_exposed_for_browsers(client) -> None:
    response = client.get(
        "/api/v1/server", headers={"Origin": "https://panel.example.com"}
    )
    exposed = response.headers.get("access-control-expose-headers", "").lower()
    assert "x-client-version" in exposed


# ---------------------------------------------------------------- 1. 可选写 token
def test_api_token_only_gates_write_methods(settings, fake_terraria) -> None:
    with _secured_client(settings, api_token="s3cret-token") as client:
        assert client.get("/api/v1/server").status_code == 200

        denied = client.post("/api/v1/server/actions", json={"action": "save"})
        assert denied.status_code == 401
        assert denied.json()["error"]["code"] == "unauthorized"

        bearer = client.post(
            "/api/v1/server/actions",
            json={"action": "save"},
            headers={"Authorization": "Bearer s3cret-token"},
        )
        assert bearer.status_code == 200

        header = client.post(
            "/api/v1/server/actions",
            json={"action": "save"},
            headers={"X-API-Token": "s3cret-token"},
        )
        assert header.status_code == 200


def test_cors_origins_can_be_narrowed(settings, fake_terraria) -> None:
    with _secured_client(
        settings, cors_origins="https://panel.example.com"
    ) as client:
        allowed = client.get(
            "/api/v1/server", headers={"Origin": "https://panel.example.com"}
        )
        assert allowed.headers["access-control-allow-origin"] == "https://panel.example.com"
        other = client.get("/api/v1/server", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in other.headers


# ---------------------------------------------------------------- 4. banlist 并发
def test_concurrent_banlist_writes_do_not_lose_lines(tmp_path) -> None:
    worlds = tmp_path / "worlds"
    worlds.mkdir()
    (worlds / "banlist.txt").write_text(
        "alice\nbob\ncarol\ndave\n", encoding="utf-8"
    )
    # 唯一临时名 + flock：多个实例并发读-改-写不会互相覆盖
    banlists = [BanList(worlds) for _ in range(4)]

    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            barrier.wait(5)
            if index < 3:
                banlists[index].remove(("alice", "bob", "carol")[index])
            else:
                banlists[index].append("erin")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert errors == []
    assert sorted(banlists[0].entries()) == ["dave", "erin"]
    assert list(worlds.glob("*.tmp")) == []


def test_banlist_remove_is_noop_for_unknown_name(tmp_path) -> None:
    worlds = tmp_path / "worlds"
    worlds.mkdir()
    (worlds / "banlist.txt").write_text("alice\n", encoding="utf-8")
    assert BanList(worlds).remove("nobody") is False
    assert BanList(worlds).entries() == ["alice"]
