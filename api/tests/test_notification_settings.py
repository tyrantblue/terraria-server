"""通知目标的可配置化：环境变量默认值 + `control/notify.json` 覆盖。

覆盖三件事：
1. 落盘/读取/重置（含文件损坏时不拖垮 API）；
2. API 的合并语义（省略 = 不改、`""` = 清空、掩码 = 不改）与校验；
3. 改完立刻生效（不需要重启容器），`POST /notifications/test` 用的是新配置。
"""

from __future__ import annotations

import dataclasses
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.core.masking import SECRET_MASK, mask_url
from app.services.notification_settings import NotificationSettings
from app.services.notifications import NotificationConfig, config_from_env
from app.services.runtime import build_runtime

FEISHU_URL = "https://open.feishu.cn/open-apis/bot/v2/hook/aaa-bbb"


class _Webhook(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        _Webhook.received.append(json.loads(body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args) -> None:  # 静音
        return


@pytest.fixture
def webhook():
    _Webhook.received = []
    server = HTTPServer(("127.0.0.1", 0), _Webhook)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/hook"
    finally:
        server.shutdown()
        thread.join(timeout=2)


# ---------------------------------------------------------------- 默认值 / 落盘
def test_status_starts_from_env_defaults(client) -> None:
    """conftest 里没配 NOTIFY_*：应当是「未启用、来源 env、缺 url」。"""
    body = client.get("/api/v1/notifications").json()
    assert body["enabled"] is False
    assert body["source"] == "env"
    assert body["provider"] == "auto"
    assert body["missing"] == ["url"]


def test_put_webhook_persists_and_enables(client, settings) -> None:
    response = client.put(
        "/api/v1/notifications/settings",
        json={
            "provider": "feishu",
            "url": FEISHU_URL,
            "events": "log_stalled,schedule_failed",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True
    assert body["provider"] == "feishu"
    assert body["format"] == "feishu"
    assert body["source"] == "file"
    assert body["events"] == ["log_stalled", "schedule_failed"]
    # webhook URL 是凭据：只回主机名
    assert body["url"] == mask_url(FEISHU_URL)
    assert "aaa-bbb" not in response.text

    # 真实值落在文件里（API 自己要用）
    saved = json.loads((settings.control_dir / "notify.json").read_text(encoding="utf-8"))
    assert saved["url"] == FEISHU_URL
    assert saved["provider"] == "feishu"
    # 立刻生效：不用重启，GET 也能看到
    assert client.get("/api/v1/notifications").json()["enabled"] is True


def test_file_overrides_env_defaults(settings) -> None:
    """环境变量给了 URL，但文件里的 provider/URL 优先。"""
    settings = dataclasses.replace(
        settings, notify_format="json", notify_webhook_url="https://env.example/hook"
    )
    service = NotificationSettings(settings.notify_settings_file, defaults=config_from_env(settings))
    assert service.load().url == "https://env.example/hook"

    service.save(
        service.load(),
        {"provider": "feishu", "url": FEISHU_URL},
    )
    loaded = service.load()
    assert loaded.url == FEISHU_URL
    assert loaded.provider_name == "feishu"
    assert loaded.source == "file"


def test_corrupt_file_falls_back_to_env(settings) -> None:
    path = settings.notify_settings_file
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    runtime = build_runtime(settings)
    assert runtime.notifier.status()["source"] == "env"
    assert runtime.notifier.enabled is False


def test_env_can_configure_qq(settings) -> None:
    settings = dataclasses.replace(
        settings,
        notify_format="qq",
        notify_qq_app_id="APPID",
        notify_qq_client_secret="SECRET",
        notify_qq_channel_id="CH1",
    )
    config = config_from_env(settings)
    assert config.is_qq is True
    assert config.missing() == []
    runtime = build_runtime(settings)
    assert runtime.notifier.enabled is True
    assert runtime.notifier.status()["format"] == "qq"


# ---------------------------------------------------------------- QQ 配置
def test_qq_requires_credentials(client) -> None:
    response = client.put(
        "/api/v1/notifications/settings",
        json={"provider": "qq", "qq": {"app_id": "APPID"}},
    )
    assert response.status_code == 400
    details = response.json()["error"]["details"]
    assert details["missing"] == ["qq.client_secret", "qq.channel_id"]


def test_qq_roundtrip_and_secret_masking(client, settings) -> None:
    created = client.put(
        "/api/v1/notifications/settings",
        json={
            "provider": "qq",
            "qq": {"app_id": "APPID", "client_secret": "S3CRET", "channel_id": "CH1"},
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["enabled"] is True and body["format"] == "qq"
    assert body["qq"]["client_secret"] == SECRET_MASK
    assert body["qq"]["client_secret_set"] is True
    assert "S3CRET" not in created.text

    # 把掩码原样传回来 = 不改 secret，但 channel_id 会更新
    updated = client.put(
        "/api/v1/notifications/settings",
        json={"qq": {"client_secret": SECRET_MASK, "channel_id": "CH2"}},
    )
    assert updated.status_code == 200
    saved = json.loads((settings.control_dir / "notify.json").read_text(encoding="utf-8"))
    assert saved["qq"]["client_secret"] == "S3CRET", "掩码不能把真实 secret 覆盖掉"
    assert saved["qq"]["channel_id"] == "CH2"

    # 空字符串 = 显式清空；但 qq 渠道少了 secret 就是不可用的配置 → 400，
    # 文件必须保持原样（不允许把服务端改成一个「保存成功但发不出去」的状态）
    cleared = client.put("/api/v1/notifications/settings", json={"qq": {"client_secret": ""}})
    assert cleared.status_code == 400
    assert cleared.json()["error"]["details"]["missing"] == ["qq.client_secret"]
    saved = json.loads((settings.control_dir / "notify.json").read_text(encoding="utf-8"))
    assert saved["qq"]["client_secret"] == "S3CRET"


def test_provider_none_is_the_off_switch(client, settings) -> None:
    """`provider=none` = 明确关闭：不需要任何传输配置，也不会再投递。"""
    client.put(
        "/api/v1/notifications/settings",
        json={"provider": "qq", "qq": {"app_id": "APPID", "client_secret": "S", "channel_id": "CH1"}},
    )
    assert client.get("/api/v1/notifications").json()["enabled"] is True

    off = client.put("/api/v1/notifications/settings", json={"provider": "none"})
    assert off.status_code == 200
    body = off.json()
    assert body["enabled"] is False
    assert body["provider"] == "none"
    assert body["missing"] == []
    assert body["format"] == "none"
    # 凭据留着（方便再打开），只是不再投递
    saved = json.loads((settings.control_dir / "notify.json").read_text(encoding="utf-8"))
    assert saved["qq"]["client_secret"] == "S"
    assert saved["provider"] == "none"

    # 再切回来 → 立刻恢复投递能力（凭据还在）
    back = client.put("/api/v1/notifications/settings", json={"provider": "qq"})
    assert back.json()["enabled"] is True


def test_masked_url_roundtrip_keeps_the_real_url(client, settings) -> None:
    client.put(
        "/api/v1/notifications/settings",
        json={"provider": "discord", "url": FEISHU_URL},
    )
    masked = client.get("/api/v1/notifications").json()["url"]
    assert masked.endswith("…")

    response = client.put("/api/v1/notifications/settings", json={"url": masked})
    assert response.status_code == 200
    saved = json.loads((settings.control_dir / "notify.json").read_text(encoding="utf-8"))
    assert saved["url"] == FEISHU_URL, "回显的掩码不能把真实 URL 覆盖掉"


def test_omitted_fields_stay_unchanged(client, settings) -> None:
    client.put(
        "/api/v1/notifications/settings",
        json={"provider": "json", "url": FEISHU_URL, "events": "player_join"},
    )
    # 只改 sandbox 之外的一个 QQ 字段：其余字段（provider/url/events）必须原样保留
    response = client.put(
        "/api/v1/notifications/settings",
        json={"qq": {"app_id": "APPID"}},
    )
    assert response.status_code == 200
    saved = json.loads((settings.control_dir / "notify.json").read_text(encoding="utf-8"))
    assert saved["provider"] == "json"
    assert saved["url"] == FEISHU_URL
    assert saved["events"] == "player_join"


# ---------------------------------------------------------------- 校验
def test_unknown_provider_is_rejected(client) -> None:
    response = client.put(
        "/api/v1/notifications/settings", json={"provider": "telegram", "url": FEISHU_URL}
    )
    assert response.status_code == 400
    assert "telegram" in response.json()["detail"]
    assert "qq" in response.json()["error"]["details"]["allowed"]


def test_webhook_provider_needs_http_url(client) -> None:
    assert (
        client.put("/api/v1/notifications/settings", json={"provider": "discord"}).status_code
        == 400
    )
    bad = client.put(
        "/api/v1/notifications/settings", json={"provider": "discord", "url": "ftp://x/y"}
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["details"]["url"] == "ftp://x/y"


def test_unknown_events_are_rejected(client) -> None:
    response = client.put(
        "/api/v1/notifications/settings",
        json={"provider": "json", "url": FEISHU_URL, "events": "player_join,typo_event"},
    )
    assert response.status_code == 400
    details = response.json()["error"]["details"]
    assert details["unknown"] == ["typo_event"]
    assert "log_stalled" in details["allowed"]


# ---------------------------------------------------------------- 重置与测试发送
def test_delete_restores_env_defaults(client, settings) -> None:
    client.put(
        "/api/v1/notifications/settings", json={"provider": "json", "url": FEISHU_URL}
    )
    assert (settings.control_dir / "notify.json").exists()

    response = client.delete("/api/v1/notifications/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "env"
    assert body["enabled"] is False
    assert not (settings.control_dir / "notify.json").exists()


def test_test_endpoint_uses_the_new_config(client, webhook) -> None:
    """改完配置立刻用 `POST /notifications/test` 验证（走的是新目标）。"""
    client.put(
        "/api/v1/notifications/settings", json={"provider": "json", "url": webhook}
    )
    result = client.post("/api/v1/notifications/test").json()
    assert result["ok"] is True, result
    assert _Webhook.received and _Webhook.received[0]["event"] == "test"


def test_test_endpoint_reports_missing_config(client) -> None:
    result = client.post("/api/v1/notifications/test").json()
    assert result["ok"] is False
    assert "url" in result["error"]


# ---------------------------------------------------------------- 事件过滤仍生效
def _wait_for(predicate, timeout: float = 3.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_event_filter_applies_after_reconfig(rt, webhook) -> None:
    rt.notify_settings.save(
        rt.notifier.config,
        {"provider": "json", "url": webhook, "events": "log_stalled"},
    )
    rt.notifier.configure(rt.notify_settings.load())
    rt.notifier.notify("player_join", "不该发")
    rt.notifier.notify("log_stalled", "应该发")

    # `notify()` 是异步的（走后台队列）：等它真的投递出去再断言，别赌时序
    assert _wait_for(lambda: any(item["event"] == "log_stalled" for item in _Webhook.received))
    events = [item["event"] for item in _Webhook.received]
    assert events == ["log_stalled"], events


def test_notification_config_dataclass_is_frozen() -> None:
    config = NotificationConfig(provider="json", url="https://x/y")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.url = "https://z/w"  # type: ignore[misc]


# ---------------------------------------------------------------- 与安全加固的配合
def test_settings_file_is_owner_only(client, settings) -> None:
    """里面有 webhook URL 与 client_secret：落盘权限必须是 0600。"""
    client.put("/api/v1/notifications/settings", json={"provider": "json", "url": FEISHU_URL})
    mode = (settings.control_dir / "notify.json").stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)


def test_write_endpoint_respects_the_api_token(settings, fake_terraria) -> None:
    """issue #17 的写操作 token 对新接口同样生效；GET 仍然只读免鉴权。"""
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.runtime import build_runtime

    secured = dataclasses.replace(settings, api_token="s3cret-token")
    with TestClient(create_app(build_runtime(secured))) as client:
        body = {"provider": "json", "url": FEISHU_URL}
        assert client.put("/api/v1/notifications/settings", json=body).status_code == 401
        assert client.delete("/api/v1/notifications/settings").status_code == 401

        allowed = client.put(
            "/api/v1/notifications/settings",
            json=body,
            headers={"Authorization": "Bearer s3cret-token"},
        )
        assert allowed.status_code == 200, allowed.text
        assert client.get("/api/v1/notifications").status_code == 200
