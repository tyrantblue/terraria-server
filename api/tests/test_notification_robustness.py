"""通知配置与投递的加固回归（本轮 review 的发现）。

每条都对应一个真实缺陷：

* 畸形 webhook URL 曾经 500 且**照样落盘**，之后 GET 500、重启时 API 起不来；
* `mask_url()` 曾经把 URL 里的 `user:pass@` 原样回给面板；
* `notify.json` 曾经用固定 `.tmp` 名，并在 rename 之后才 chmod（有一段可读窗口）；
* 投递异常的文本会把带凭据的 URL 写进日志与 API 响应；
* notifier 线程遇到未预料的异常会静默退出。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from app.core.masking import mask_url
from app.services.notification_settings import NotificationSettings
from app.services.notifications import (
    NotificationConfig,
    Notifier,
    config_from_env,
)


# ---------------------------------------------------------------- ① 畸形 URL
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://[::1",                     # 未闭合的 IPv6 字面量
        "https://open.feishu.cn／hook",     # 全角斜杠（中文输入法）
        "https://hooks.example.com:99999/x",  # 非法端口
        "hooks.example.com/x",             # 没有 scheme
        "ftp://hooks.example.com/x",       # 非 http(s)
    ],
)
def test_malformed_webhook_url_is_rejected_and_not_persisted(client, settings, bad_url) -> None:
    response = client.put(
        "/api/v1/notifications/settings",
        json={"provider": "auto", "url": bad_url},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "bad_request"
    # 关键：坏配置不能落盘（落盘后 GET 会 500、重启会起不来）
    assert not (settings.control_dir / "notify.json").exists()
    assert client.get("/api/v1/notifications").status_code == 200


def test_a_persisted_bad_url_does_not_brick_startup(settings) -> None:
    """就算文件是手工写坏的，加载/启动/查状态也不能抛异常。"""
    path = settings.control_dir / "notify.json"
    path.write_text(
        '{"provider": "auto", "url": "http://[::1", "events": "", "qq": {}}',
        encoding="utf-8",
    )
    config = NotificationSettings(path, defaults=config_from_env(settings)).load()
    assert config.url == "http://[::1"

    notifier = Notifier(config=config)
    notifier.start()  # 旧实现：mask_url() → ValueError，lifespan 直接失败
    try:
        status = notifier.status()
    finally:
        notifier.stop()
    assert status["url"].endswith("…")
    assert "::1" not in status["url"]


def test_mask_url_keeps_only_the_host() -> None:
    assert mask_url("https://discord.com/api/webhooks/1/abc") == "https://discord.com/…"
    assert mask_url("") == ""
    # ② basic-auth 也是凭据，不能出现在掩码里
    masked = mask_url("https://botuser:s3cr3t-pass@hooks.example.com/services/T/B/X")
    assert masked == "https://hooks.example.com/…"
    assert "s3cr3t" not in masked and "botuser" not in masked
    # 畸形 URL 退化成省略号，而不是抛异常
    assert mask_url("http://[::1") == "…"


# ---------------------------------------------------------------- ③ QQ 高级覆盖
def test_qq_override_urls_are_validated(client) -> None:
    response = client.put(
        "/api/v1/notifications/settings",
        json={"qq": {"token_url": "ftp://evil.example/token"}},
    )
    assert response.status_code == 400
    assert response.json()["error"]["details"]["qq.token_url"] == "ftp://evil.example/token"


def test_masked_qq_override_round_trips_as_keep(client) -> None:
    """视图里的 api_base/token_url 现在是掩码；把它原样写回必须等于「不改」。"""
    created = client.put(
        "/api/v1/notifications/settings",
        json={
            "provider": "qq",
            "qq": {
                "app_id": "APPID",
                "client_secret": "SECRET",
                "channel_id": "CH1",
                "api_base": "https://gateway.example",
            },
        },
    )
    assert created.status_code == 200
    view = created.json()["qq"]
    assert view["api_base"] == "https://gateway.example/…"

    # 面板「读出来 → 原样写回去」：掩码不能被当成真实值存下来
    again = client.put("/api/v1/notifications/settings", json={"qq": {"api_base": view["api_base"]}})
    assert again.status_code == 200
    assert again.json()["qq"]["api_base"] == "https://gateway.example/…"

    from app.services.notification_settings import NotificationSettings

    stored = NotificationSettings(
        client.app.state.runtime.settings.notify_settings_file,
        defaults=NotificationConfig(source="env"),
    ).load()
    assert stored.qq_api_base == "https://gateway.example"


# ---------------------------------------------------------------- ④ 落盘
def test_settings_file_is_written_private_and_atomically(rt, monkeypatch) -> None:
    target = rt.settings.control_dir / "notify.json"
    real_replace = os.replace
    seen: dict[str, str] = {}

    def spy(src, dst):
        seen["temp"] = oct(os.stat(src).st_mode & 0o777)
        real_replace(src, dst)
        seen["dest"] = oct(os.stat(dst).st_mode & 0o777)

    monkeypatch.setattr(os, "replace", spy)
    rt.notify_settings.save(
        NotificationConfig(source="env"),
        {"provider": "json", "url": "https://hooks.example.com/secret"},
    )
    monkeypatch.undo()

    # rename 之前就必须是 0600：旧实现是 rename 之后再 chmod，中间有一段可读窗口，
    # 而且进程若在窗口内退出，含密钥的文件会永久停在 0644。
    assert seen == {"temp": "0o600", "dest": "0o600"}
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert list(rt.settings.control_dir.glob("*.tmp")) == []


def test_concurrent_settings_saves_do_not_collide(rt) -> None:
    """并发 PUT（双击保存/面板重试）不再共用同一个 .tmp 而互相 FileNotFoundError。"""
    errors: list[str] = []

    def worker() -> None:
        try:
            for _ in range(20):
                rt.notify_settings.save(
                    NotificationConfig(source="env"),
                    {"provider": "json", "url": "https://hooks.example.com/secret"},
                )
        except Exception as exc:  # noqa: BLE001 - 收集后统一断言
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert list(rt.settings.control_dir.glob("*.tmp")) == []


# ---------------------------------------------------------------- ⑧ 合并语义
def test_provider_empty_keeps_but_events_empty_clears() -> None:
    settings = NotificationSettings(Path("/nonexistent/notify.json"), defaults=NotificationConfig())
    current = NotificationConfig(
        provider="qq", qq_app_id="A", qq_client_secret="S", qq_channel_id="C",
        events="player_join", source="file",
    )
    merged = settings.merge(current, {"provider": "", "events": ""})
    assert merged.provider_name == "qq", "传空不是清空，也不是回落到 auto"
    assert merged.events == "", "events 传空 = 全部事件（与 CHANGELOG 一致）"


# ---------------------------------------------------------------- ⑥⑦ 投递健壮性
def test_delivery_error_is_redacted() -> None:
    """httplib 会把 URL 的 userinfo 当端口报错，这段文本会进日志和 API 响应。"""
    notifier = Notifier(
        config=NotificationConfig(
            provider="json",
            url="https://botuser:s3cr3t-pass@no-such-host.invalid/hook",
        )
    )
    delivery = notifier._deliver("test", "标题", {}, "info")
    assert delivery.ok is False
    assert "s3cr3t" not in (delivery.error or "")
    assert "s3cr3t" not in notifier.status()["url"]


def test_notifier_loop_survives_an_unexpected_exception(monkeypatch) -> None:
    notifier = Notifier(
        config=NotificationConfig(provider="json", url="https://hooks.example.com/x")
    )
    handled: list[str] = []

    def flaky(event, title, detail, level):
        handled.append(event)
        if len(handled) == 1:
            raise RuntimeError("boom")
        notifier._stop.set()  # 处理完第二条就收工

    monkeypatch.setattr(notifier, "_deliver", flaky)
    notifier.start()
    notifier.notify("first", "t")
    notifier.notify("second", "t")

    deadline = time.time() + 5
    while len(handled) < 2 and time.time() < deadline:
        time.sleep(0.02)
    notifier.stop()

    assert handled == ["first", "second"], "第一次抛异常后投递线程不能死"


def test_status_events_are_trimmed_and_deduped() -> None:
    notifier = Notifier(
        config=NotificationConfig(
            provider="json",
            url="https://hooks.example.com/x",
            events="player_join, log_stalled ,player_join,",
        )
    )
    assert notifier.status()["events"] == ["log_stalled", "player_join"]


# ---------------------------------------------------------------- ⑧ 长任务查询
def test_operations_state_query_is_validated(client) -> None:
    assert client.get("/api/v1/operations", params={"state": "bogus"}).status_code == 422
    assert client.get("/api/v1/operations", params={"state": "running"}).status_code == 200
