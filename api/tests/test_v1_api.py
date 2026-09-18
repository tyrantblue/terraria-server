"""v1 接口测试。

覆盖新增的资源化接口、长任务、配置持久化、命令白名单与弃用埋点。
全部跑在假 Terraria 上，不碰真实服务端。
"""

from __future__ import annotations

import time

import pytest

from app.services import config_service


def wait_operation(client, operation_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/operations/{operation_id}").json()
        if body["state"] in ("succeeded", "failed"):
            return body
        time.sleep(0.1)
    raise AssertionError(f"operation {operation_id} 未在 {timeout}s 内结束")


# ---------------------------------------------------------------- server
def test_server_state_combines_everything(client) -> None:
    body = client.get("/api/v1/server").json()
    assert body["version"] == "1.4.5.8"
    assert body["port"] == 7777
    assert body["max_players"] == 255
    assert body["players"]["online"] == 0
    assert body["world"]["file"] == "WSD.wld"
    assert body["world"]["active"] is True
    assert body["config"]["world"] == "/worlds/WSD.wld"


def test_players_with_ip(client, fake_terraria) -> None:
    fake_terraria.players = ("ユノの犬 (203.0.113.10:26557)", "CTQ (198.51.100.30:44176)")
    body = client.get("/api/v1/players").json()
    assert body["online"] == 2
    assert body["players"][0] == {"name": "ユノの犬", "ip": "203.0.113.10", "port": 26557}
    assert body["players"][1]["name"] == "CTQ"


def test_kick_unknown_player_is_404(client) -> None:
    response = client.post("/api/v1/players/__nobody__/kick")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_kick_online_player(client, fake_terraria) -> None:
    fake_terraria.players = ("CTQ (198.51.100.30:44176)",)
    assert client.post("/api/v1/players/CTQ/kick").status_code == 200


def test_broadcast_requires_message(client) -> None:
    assert client.post("/api/v1/broadcast", json={"message": "hi"}).status_code == 200
    assert client.post("/api/v1/broadcast", json={"message": "  "}).status_code == 400


def test_bans_endpoint_reports_missing_file(client) -> None:
    body = client.get("/api/v1/bans").json()
    assert body["bans"] == []
    assert body["exists"] is False
    assert "unban" in body["note"]


def test_unban_missing_entry_is_404(client) -> None:
    assert client.delete("/api/v1/players/__nobody__/ban").status_code == 404


def test_server_time_phase_validated(client) -> None:
    assert client.post("/api/v1/server/time", json={"phase": "noon"}).status_code == 200
    assert client.post("/api/v1/server/time", json={"phase": "teatime"}).status_code == 422


def test_server_action(client) -> None:
    assert client.post("/api/v1/server/actions", json={"action": "save"}).json() == {"ok": True}
    assert client.post("/api/v1/server/actions", json={"action": "nope"}).status_code == 422


def test_restart_returns_operation(client) -> None:
    response = client.post("/api/v1/server/restart")
    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "server.restart"
    finished = wait_operation(client, body["operation_id"])
    assert finished["state"] == "succeeded", finished


# ---------------------------------------------------------------- console
def test_console_lines_are_classified(client, fake_terraria) -> None:
    fake_terraria.inject_line("Alice has joined.")
    body = client.get("/api/v1/console", params={"tail": 50}).json()
    kinds = {line["kind"] for line in body["lines"]}
    assert "player_join" in kinds
    assert "startup" in kinds
    assert body["cursor"] > 0
    assert all("Usage: kick" not in line["text"] for line in body["lines"])


def test_console_since_cursor_only_returns_new_lines(client, fake_terraria) -> None:
    cursor = client.get("/api/v1/console", params={"tail": 5}).json()["cursor"]
    fake_terraria.inject_line("Bob has left.")
    body = client.get("/api/v1/console", params={"since": cursor}).json()
    assert [line["text"] for line in body["lines"]] == ["Bob has left."]
    assert body["cursor"] > cursor


def test_console_command_whitelist(client) -> None:
    assert client.post("/api/v1/console/commands", json={"command": "save"}).status_code == 200
    blocked = client.post("/api/v1/console/commands", json={"command": "exit"})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "forbidden"
    assert client.post("/api/v1/console/commands", json={"command": "rm -rf /"}).status_code == 403


def test_console_commands_are_audited(client) -> None:
    client.post("/api/v1/console/commands", json={"command": "seed"})
    entries = client.get("/api/v1/console/audit").json()["entries"]
    assert entries and entries[0]["command"] == "seed"
    assert entries[0]["actor"] == "api"


# ---------------------------------------------------------------- config
def test_config_view_normalizes_world(client) -> None:
    body = client.get("/api/v1/config").json()
    assert body["values"]["world"] == "WSD.wld"
    assert "maxplayers" in body["editable_keys"]
    assert "port" in body["restart_keys"]
    assert "motd" in body["runtime_keys"]


def test_config_put_persists_and_applies_runtime(client, settings) -> None:
    response = client.put(
        "/api/v1/config",
        json={"values": {"motd": "new motd"}, "apply": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] == ["motd"]
    assert body["applied"] == ["motd"]
    assert "motd=new motd" in settings.config_file.read_text(encoding="utf-8")


def test_config_put_restart_key_returns_operation(client) -> None:
    response = client.put(
        "/api/v1/config",
        json={"values": {"port": 7778}, "apply": True},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["requires_restart"] == ["port"]
    finished = wait_operation(client, body["operation_id"])
    assert finished["state"] == "succeeded", finished


def test_config_put_unknown_key_is_400(client) -> None:
    response = client.put("/api/v1/config", json={"values": {"evil": "1"}})
    assert response.status_code == 400
    assert "editable_keys" in response.json()["error"]["details"]


def test_config_put_validates_ranges(client) -> None:
    assert client.put("/api/v1/config", json={"values": {"maxplayers": 0}}).status_code == 400
    assert client.put("/api/v1/config", json={"values": {"port": 99999}}).status_code == 400
    assert client.put("/api/v1/config", json={"values": {"world": "../etc/passwd"}}).status_code == 400


def test_low_maxplayers_needs_confirmation(client) -> None:
    """8 个槽位会被扫描连接「假满员」拖垮，必须显式确认（见 docs/connection-guard.md）。"""
    response = client.put("/api/v1/config", json={"values": {"maxplayers": 8}})
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "phantom-full"

    ok = client.put(
        "/api/v1/config",
        json={"values": {"maxplayers": 8}, "confirm_low_max_players": True},
    )
    assert ok.status_code == 200


# ---------------------------------------------------------------- 敏感配置（#6）
def _set_password_line(settings, password: str) -> None:
    settings.config_file.write_text(
        settings.config_file.read_text(encoding="utf-8") + f"password={password}\n",
        encoding="utf-8",
    )


def test_config_get_never_echoes_plaintext_password(client, settings) -> None:
    """issue #6：GET 不该把 serverconfig.txt 里的明文密码回给任何客户端。"""
    _set_password_line(settings, "123456")

    body = client.get("/api/v1/config").json()
    assert body["values"]["password"] == config_service.SECRET_MASK
    assert body["password_set"] is True

    server = client.get("/api/v1/server").json()
    assert server["config"]["password"] == config_service.SECRET_MASK
    assert server["password_set"] is True

    assert "123456" not in client.get("/api/v1/config").text
    assert "123456" not in client.get("/api/v1/server").text


def test_config_get_reports_missing_and_empty_password(client, settings) -> None:
    """未设置与设置为空都要能区分出来，且都不掩码（没什么可藏的）。"""
    body = client.get("/api/v1/config").json()
    assert body["password_set"] is False
    assert body["values"].get("password", "") == ""

    _set_password_line(settings, "")
    body = client.get("/api/v1/config").json()
    assert body["password_set"] is False
    assert body["values"]["password"] == ""


def test_config_put_password_works_and_stays_write_only(client, settings) -> None:
    response = client.put("/api/v1/config", json={"values": {"password": "s3cret-pw"}})
    assert response.status_code == 200
    assert response.json()["persisted"] == ["password"]
    assert "password=s3cret-pw" in settings.config_file.read_text(encoding="utf-8")

    # 写完立刻读回：仍然只有掩码
    body = client.get("/api/v1/config").json()
    assert body["values"]["password"] == config_service.SECRET_MASK


def test_config_put_rejects_roundtripped_mask(client) -> None:
    """前端把 GET 的 values 原样 PUT 回来时，掩码不能被当成新密码。"""
    response = client.put(
        "/api/v1/config", json={"values": {"password": config_service.SECRET_MASK}}
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"]["key"] == "password"


def test_config_put_empty_password_clears_it(client, settings) -> None:
    """空字符串 = 清空密码（面板的「留空即移除」依赖这个语义，见 issue #6）。"""
    _set_password_line(settings, "123456")
    assert client.get("/api/v1/config").json()["password_set"] is True

    response = client.put(
        "/api/v1/config", json={"values": {"password": ""}, "apply": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] == ["password"]
    assert body["applied"] == ["password"]

    assert "password=\n" in settings.config_file.read_text(encoding="utf-8")
    assert client.get("/api/v1/config").json()["password_set"] is False


# ---------------------------------------------------------------- 控制台流（#7）
def test_console_stream_replay_uses_real_offsets(client, fake_terraria) -> None:
    """issue #7：回放帧的 offset 必须是真实行偏移，不能恒为 -1。"""
    fake_terraria.inject_line("Alice has joined.")

    with client.websocket_connect("/api/v1/console/stream") as websocket:
        frames = []
        while True:
            frame = websocket.receive_json()
            frames.append(frame)
            if frame["type"] == "hello":
                break

    assert frames[-1]["type"] == "hello"
    assert frames[-1]["cursor"] > 0

    lines = [frame for frame in frames if frame["type"] == "console.line"]
    assert lines, "回放应当至少包含启动横幅"
    offsets = [frame["offset"] for frame in lines]
    assert all(offset >= 0 for offset in offsets), offsets
    assert len(set(offsets)) == len(offsets), "回放行的 offset 必须唯一（去重键）"
    assert offsets == sorted(offsets)
    assert max(offsets) < frames[-1]["cursor"]
    assert any("Alice has joined." in frame["text"] for frame in lines)


def test_console_stream_matches_rest_offsets(client, fake_terraria) -> None:
    """同一条日志行，REST 与 WS 给出的 offset 必须一致。"""
    fake_terraria.inject_line("Bob has left.")
    rest = {
        line["offset"]: line["text"]
        for line in client.get("/api/v1/console", params={"tail": 20}).json()["lines"]
    }

    with client.websocket_connect("/api/v1/console/stream") as websocket:
        replay = {}
        while True:
            frame = websocket.receive_json()
            if frame["type"] == "hello":
                break
            replay[frame["offset"]] = frame["text"]

    for offset, text in replay.items():
        assert rest.get(offset) == text


# ---------------------------------------------------------------- worlds
def test_worlds_list_and_backup_dir(client) -> None:
    body = client.get("/api/v1/worlds").json()
    assert [w["file"] for w in body["worlds"]] == ["WSD.wld", "gogogo.wld"]
    assert body["active_world"] == "WSD.wld"
    assert "backup" in body["backup_dir"]


def test_world_upload_and_delete(client) -> None:
    created = client.post(
        "/api/v1/worlds",
        files={"file": ("extra.wld", b"bytes", "application/octet-stream")},
    )
    assert created.status_code == 201
    assert created.json()["file"] == "extra.wld"
    assert client.delete("/api/v1/worlds/extra.wld").status_code == 200
    assert client.delete("/api/v1/worlds/extra.wld").status_code == 404


def test_cannot_delete_active_world(client) -> None:
    response = client.delete("/api/v1/worlds/WSD.wld")
    assert response.status_code == 409


def test_world_activate_is_an_operation(client) -> None:
    response = client.post("/api/v1/worlds/gogogo.wld/activate")
    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "world.activate"
    finished = wait_operation(client, body["operation_id"])
    assert finished["state"] == "succeeded", finished
    assert finished["result"]["world"] == "gogogo.wld"


def test_world_backup_creates_directory(client) -> None:
    response = client.post("/api/v1/worlds/WSD.wld/backup")
    assert response.status_code == 202
    finished = wait_operation(client, response.json()["operation_id"])
    assert finished["state"] == "succeeded", finished

    backups = client.get("/api/v1/backups").json()["backups"]
    assert backups and backups[0]["name"] == finished["result"]["backup"]
    assert "WSD.wld" in finished["result"]["files"]


def test_operations_unknown_id_is_404(client) -> None:
    assert client.get("/api/v1/operations/deadbeef").status_code == 404


def test_exclusive_operations_do_not_overlap(client, fake_terraria) -> None:
    """两个会重启服务端的操作不能同时跑。"""
    first = client.post("/api/v1/worlds/gogogo.wld/activate")
    assert first.status_code == 202
    second = client.post("/api/v1/worlds/WSD.wld/activate")
    assert second.status_code in (202, 409)
    if second.status_code == 409:
        assert second.json()["error"]["code"] == "conflict"
    wait_operation(client, first.json()["operation_id"])


# ---------------------------------------------------------------- 握手与旧接口
def test_meta_handshake(client) -> None:
    body = client.get("/api/meta").json()
    assert body["api_version"] == "2.0.1"
    assert body["min_client_version"] == "1.4.0"
    assert body["server_version"] == "1.4.5.8"
    assert "world.switch" in body["capabilities"]
    # 旧接口删完后没有弃用项了；字段保留但恒为空
    assert body["deprecations"] == []
    assert body["links"]["openapi"] == "/openapi.json"


@pytest.mark.parametrize(
    "path",
    [
        "/api/server/status",
        "/api/server/players",
        "/api/server/console",
        "/api/world/list",
        "/api/meta/usage",
    ],
)
def test_legacy_routes_are_gone(client, path) -> None:
    """2.0.0：旧 `/api/*` 返回 404，而不是继续带着弃用头工作。"""
    assert client.get(path).status_code == 404
