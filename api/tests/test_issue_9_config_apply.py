"""issue #9：`PUT /api/v1/config` 不能「文件已落盘、运行态没生效」就假成功。

判定「需不需要 apply」不能只看文件差异，要看运行态能否对齐；apply 失败时要
让调用方看出「写了但没生效」，并且同样的请求可以重试成功。
"""

from __future__ import annotations

import time

from app.core.errors import ConsoleUnavailable
from app.services.server_service import ServerService


def wait_operation(client, operation_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/operations/{operation_id}").json()
        if body["state"] in ("succeeded", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError("operation 未结束")


def wait_for_command(fake, needle: str, timeout: float = 3.0) -> bool:
    """控制台命令是经 FIFO 异步送达的，断言前要等假服务端收到。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(needle in command for command in fake.commands):
            return True
        time.sleep(0.02)
    return False


def _write_config(settings, extra: str) -> None:
    settings.config_file.write_text(
        settings.config_file.read_text(encoding="utf-8") + extra, encoding="utf-8"
    )


def test_same_value_is_reapplied_even_when_file_already_matches(
    client, settings, fake_terraria
) -> None:
    """文件里已经是 B、运行态还是旧值时，PUT 同一个 B 必须真的应用。"""
    _write_config(settings, "motd=B\n")

    response = client.put("/api/v1/config", json={"values": {"motd": "B"}, "apply": True})
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] == []              # 文件差异为空
    assert body["applied"] == ["motd"]        # 但运行态被对齐了
    assert wait_for_command(fake_terraria, "motd B")


def test_apply_failure_reports_persisted_then_retry_succeeds(
    client, settings, monkeypatch
) -> None:
    """apply 失败 → 503，但 details 说明文件已写入；重试同样的值必须真生效。"""
    calls = {"n": 0}
    original = ServerService.set_motd

    def flaky(self, motd: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConsoleUnavailable("控制台暂时不可用")
        return original(self, motd)

    monkeypatch.setattr(ServerService, "set_motd", flaky)

    first = client.put("/api/v1/config", json={"values": {"motd": "C"}, "apply": True})
    assert first.status_code == 503
    details = first.json()["error"]["details"]
    assert details["persisted"] == ["motd"]
    assert details["applied"] == []
    assert details["pending"] == ["motd"]
    assert "motd=C" in settings.config_file.read_text(encoding="utf-8")

    # 关键：同样的请求再来一次，不能是 200 + applied=[] 的假成功
    second = client.put("/api/v1/config", json={"values": {"motd": "C"}, "apply": True})
    assert second.status_code == 200
    assert second.json()["applied"] == ["motd"]


def test_restart_key_is_reapplied_when_file_already_matches(client, settings) -> None:
    """重启类键同理：文件已是目标值，apply=True 仍要提交重启把它真正生效。"""
    _write_config(settings, "port=7778\n")

    response = client.put("/api/v1/config", json={"values": {"port": 7778}, "apply": True})
    assert response.status_code == 202
    body = response.json()
    assert body["changed"] == []
    assert body["requires_restart"] == ["port"]
    assert body["operation_id"]
    assert wait_operation(client, body["operation_id"])["state"] == "succeeded"


def test_without_apply_nothing_is_applied_but_file_is_persisted(client, settings) -> None:
    response = client.put("/api/v1/config", json={"values": {"motd": "D"}})
    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] == ["motd"]
    assert body["applied"] == []
    assert body["operation_id"] is None
    assert "motd=D" in settings.config_file.read_text(encoding="utf-8")
