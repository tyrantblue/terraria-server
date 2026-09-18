"""守卫可视化：状态读取、命令下发、守卫不在时的降级。

API 侧用「假守卫」验证：测试自己扮演守卫消费命令文件并回写结果，
不需要真的 ipset/iptables。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from app.services.guard_client import GuardClient, GuardUnavailable


class FakeGuard(threading.Thread):
    """极简版的守卫：消费命令并把它记下来，然后回写结果。"""

    def __init__(self, state_file: Path, command_file: Path) -> None:
        super().__init__(daemon=True)
        self.state_file = state_file
        self.command_file = command_file
        self.commands: list[dict] = []
        self.allow: list[dict] = [{"ip": "203.0.113.10", "source": "static", "expires_at": None}]
        self.banned: list[dict] = [{"ip": "203.0.113.9", "expires_at": time.time() + 600}]
        self.results: dict[str, dict] = {}
        self._stop = threading.Event()
        self._offset = 0

    def run(self) -> None:  # pragma: no cover - 线程体
        while not self._stop.is_set():
            self._consume()
            self.publish()
            self._stop.wait(0.2)

    def stop(self) -> None:
        self._stop.set()

    def _consume(self) -> None:
        if not self.command_file.exists():
            return
        size = self.command_file.stat().st_size
        if size <= self._offset:
            return
        with self.command_file.open("r", encoding="utf-8") as handle:
            handle.seek(self._offset)
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                self.commands.append(payload)
                self._apply(payload)
            self._offset = handle.tell()

    def _apply(self, payload: dict) -> None:
        action = payload["action"]
        ip = payload.get("args", {}).get("ip")
        message = f"{action} {ip}" if ip else action
        if action == "ban" and ip:
            self.banned.append({"ip": ip, "expires_at": time.time() + 3600})
        if action == "unban" and ip:
            self.banned = [item for item in self.banned if item["ip"] != ip]
        if action == "allow" and ip:
            self.allow.append({"ip": ip, "source": "static", "expires_at": None})
        if action == "disallow" and ip:
            self.allow = [item for item in self.allow if item["ip"] != ip]
        self.results[payload["id"]] = {"ok": True, "message": message, "ts": time.time()}

    def publish(self) -> None:
        state = {
            "updated_at": time.time(),
            "port": 7777,
            "allowlist_only": False,
            "allow": self.allow,
            "banned": self.banned,
            "counters": {"bans_total": len(self.banned), "commands_total": len(self.commands),
                         "learned_total": 0, "degraded_console": 0},
            "results": self.results,
        }
        tmp = self.state_file.with_name(self.state_file.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(self.state_file)


@pytest.fixture
def guard(rt) -> FakeGuard:
    fake = FakeGuard(
        rt.settings.control_dir / "guard-state.json",
        rt.settings.control_dir / "guard-commands.jsonl",
    )
    fake.start()
    time.sleep(0.3)
    try:
        yield fake
    finally:
        fake.stop()


def test_state_without_guard_is_available_false(rt) -> None:
    """守卫没运行时也要能返回，而不是 500——面板要显示"守卫已停止"。"""
    body = __import__("app.api.v1.guard", fromlist=["x"])._guard_state(rt, allow_unavailable=True)
    assert body["available"] is False
    assert body["banned"] == []


def test_state_without_guard_returns_full_shape(rt) -> None:
    """issue #8：守卫不在时也必须返回**完整**结构，客户端不用写 `?? []` / `?? 0`。"""
    body = __import__("app.api.v1.guard", fromlist=["x"])._guard_state(rt, allow_unavailable=True)
    assert set(body) == {
        "available", "stale", "age", "updated_at", "port",
        "allowlist_only", "allow", "banned", "counters",
    }
    assert body["allow"] == []
    assert body["counters"] == {
        "bans_total": 0, "commands_total": 0, "learned_total": 0, "degraded_console": 0,
    }


def test_guard_contract_marks_every_field_required() -> None:
    """契约（OpenAPI）也要表达同一件事：GuardState 的字段没有「可能缺失」。"""
    from app.main import create_app

    schemas = create_app().openapi()["components"]["schemas"]
    assert set(schemas["GuardState"]["required"]) == {
        "available", "stale", "age", "updated_at", "port",
        "allowlist_only", "allow", "banned", "counters",
    }
    assert set(schemas["GuardCounters"]["required"]) == {
        "bans_total", "commands_total", "learned_total", "degraded_console",
    }


def test_state_is_read_from_the_guard(client, guard) -> None:
    body = client.get("/api/v1/guard").json()
    assert body["available"] is True and body["stale"] is False
    assert body["port"] == 7777
    assert body["allow"][0]["ip"] == "203.0.113.10"
    assert body["banned"][0]["ip"] == "203.0.113.9"
    assert body["counters"]["bans_total"] == 1


def test_ban_and_unban_round_trip(client, guard) -> None:
    created = client.post("/api/v1/guard/bans", json={"ip": "198.51.100.7", "seconds": 3600})
    assert created.status_code == 200
    assert created.json()["ok"] is True
    assert any(item["ip"] == "198.51.100.7" for item in client.get("/api/v1/guard").json()["banned"])

    removed = client.delete("/api/v1/guard/bans/198.51.100.7")
    assert removed.status_code == 200
    assert not any(item["ip"] == "198.51.100.7" for item in client.get("/api/v1/guard").json()["banned"])
    assert [c["action"] for c in guard.commands] == ["ban", "unban"]


def test_allow_and_disallow(client, guard) -> None:
    assert client.post("/api/v1/guard/allow", json={"ip": "192.0.2.7"}).status_code == 200
    assert any(item["ip"] == "192.0.2.7" for item in client.get("/api/v1/guard").json()["allow"])
    assert client.delete("/api/v1/guard/allow/192.0.2.7").status_code == 200
    assert not any(item["ip"] == "192.0.2.7" for item in client.get("/api/v1/guard").json()["allow"])


def test_reload(client, guard) -> None:
    assert client.post("/api/v1/guard/reload").json()["ok"] is True
    assert guard.commands[-1]["action"] == "reload"


def test_bad_ip_is_rejected(client, guard) -> None:
    assert client.post("/api/v1/guard/bans", json={"ip": "not-an-ip"}).status_code == 503
    assert client.post("/api/v1/guard/allow", json={"ip": "1.2.3.4; rm -rf /"}).status_code == 503
    assert guard.commands == []


def test_missing_guard_returns_503(rt) -> None:
    """守卫不在时下发命令要明确失败（503），不能假装成功。"""
    client = GuardClient(
        rt.settings.control_dir / "nope.json",
        rt.settings.control_dir / "guard-commands.jsonl",
        stale_after=0.01,
    )
    with pytest.raises(GuardUnavailable):
        client.state()
    with pytest.raises(GuardUnavailable):
        client.submit("ban", {"ip": "1.2.3.4"}, timeout=0.3)


def test_stale_state_is_flagged(rt, guard) -> None:
    client = GuardClient(
        rt.settings.control_dir / "guard-state.json",
        rt.settings.control_dir / "guard-commands.jsonl",
        stale_after=0.0,
    )
    assert client.state()["stale"] is True
