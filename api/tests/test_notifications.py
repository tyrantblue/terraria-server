"""事件通知：载荷格式、事件过滤、URL 掩码、失败记录、日志事件识别。"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services.log_events import LogEventWatcher
from app.services.notifications import Notifier, mask_url


class _Collector(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        _Collector.received.append(json.loads(body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args) -> None:  # 静音
        return


@pytest.fixture
def http_webhook():
    _Collector.received = []
    server = HTTPServer(("127.0.0.1", 0), _Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/hook"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_disabled_notifier_does_nothing() -> None:
    notifier = Notifier("")
    notifier.start()
    notifier.notify("player_join", "x")
    assert notifier.status()["enabled"] is False
    assert notifier.test()["ok"] is False


def test_url_is_masked() -> None:
    assert mask_url("https://discord.com/api/webhooks/123/abc") == "https://discord.com/…"
    assert mask_url("") == ""


def test_json_payload_roundtrip(http_webhook) -> None:
    notifier = Notifier(http_webhook, fmt="json")
    notifier.start()
    try:
        notifier.notify("backup_done", "备份完成", level="success", detail={"backup": "20260101"})
        assert wait_for(lambda: _Collector.received)
    finally:
        notifier.stop()
    payload = _Collector.received[0]
    assert payload["event"] == "backup_done"
    assert payload["level"] == "success"
    assert payload["detail"] == {"backup": "20260101"}


def test_discord_and_slack_payload_shapes() -> None:
    notifier = Notifier("https://discord.com/api/webhooks/x/y", fmt="auto")
    assert "content" in notifier._payload("e", "标题", {}, "info")
    slack = Notifier("https://hooks.slack.com/services/x", fmt="auto")
    assert "text" in slack._payload("e", "标题", {}, "info")


def test_event_filter(http_webhook) -> None:
    notifier = Notifier(http_webhook, fmt="json", events="player_join")
    notifier.start()
    try:
        notifier.notify("player_leave", "不该发")
        notifier.notify("player_join", "应该发")
        assert wait_for(lambda: _Collector.received)
        time.sleep(0.1)
    finally:
        notifier.stop()
    assert [item["event"] for item in _Collector.received] == ["player_join"]


def test_delivery_failure_is_recorded() -> None:
    # 127.0.0.1:9 通常直接拒绝连接，用来验证失败也会记账
    notifier = Notifier("http://127.0.0.1:9/hook", fmt="json")
    result = notifier.test()
    assert result["ok"] is False
    assert notifier.status()["deliveries"][0]["ok"] is False


def test_status_never_leaks_the_url(http_webhook) -> None:
    notifier = Notifier(http_webhook, fmt="json")
    assert "hook" not in notifier.status()["url"]


# ---------------------------------------------------------------- 日志事件
class RecordingNotifier(Notifier):
    def __init__(self) -> None:
        super().__init__("http://example.invalid/hook")
        self.events: list[tuple[str, str]] = []

    def notify(self, event, title, *, level="info", detail=None) -> None:  # type: ignore[override]
        self.events.append((event, title))


@pytest.fixture
def watcher(rt):
    notifier = RecordingNotifier()
    return LogEventWatcher(rt.reader, notifier), notifier


def test_watcher_classifies_events(watcher) -> None:
    log_watcher, notifier = watcher
    assert log_watcher.handle(": ユノの犬 has joined.") == "player_join"
    assert log_watcher.handle("CTQ has left.") == "player_leave"
    assert log_watcher.handle("203.0.113.9:1 was booted: This server is full right now") == "player_booted"
    assert log_watcher.handle("Listening on port 7777") == "server_up"
    assert log_watcher.handle("Unhandled Exception") == "server_error"
    assert log_watcher.handle("Saving world data: 42%") is None

    events = [event for event, _ in notifier.events]
    assert events == ["player_join", "player_leave", "player_booted", "server_up", "server_error"]


def test_watcher_suppresses_repeated_errors(watcher) -> None:
    log_watcher, notifier = watcher
    log_watcher.handle("Unhandled Exception")
    log_watcher.handle("Unhandled Exception")
    assert [event for event, _ in notifier.events] == ["server_error"]


def test_notifications_api(client) -> None:
    body = client.get("/api/v1/notifications").json()
    assert body["enabled"] is False
    assert client.post("/api/v1/notifications/test").json()["ok"] is False


def test_watcher_handles_timestamped_lines(watcher) -> None:
    """start.sh 加了 [时间戳] 之后，事件识别不能失效。"""
    log_watcher, notifier = watcher
    ts = "[2026-09-17 17:01:02] "
    assert log_watcher.handle(ts + ": ユノの犬 has joined.") == "player_join"
    assert log_watcher.handle(ts + "CTQ has left.") == "player_leave"
    assert log_watcher.handle(ts + ": Listening on port 7777") == "server_up"
    assert [event for event, _ in notifier.events] == ["player_join", "player_leave", "server_up"]
