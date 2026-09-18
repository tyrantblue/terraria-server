"""QQ 频道机器人客户端（issue：通知目标可配置）。

用本地假 HTTP 服务替掉官方域名：验证令牌换取/缓存/失效重试、
发消息的请求头与载荷，以及两种错误信封（换 token 用 `code`，发消息用 `err_code`）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services.qq_bot import QQBotError, QQChannelClient


class _FakeQQ(BaseHTTPRequestHandler):
    token_requests = 0
    messages: list[dict] = []
    headers: list[dict] = []
    token_payload: dict = {"access_token": "T1", "expires_in": 7200}
    token_status = 200
    message_payload: dict = {"id": "MSG1"}
    message_status = 200
    #: 非空时直接回这段字节（用来模拟「响应不是 JSON」）
    raw_body: bytes | None = None

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode() if length else ""
        body = json.loads(raw) if raw else {}
        if self.path == "/app/getAppAccessToken":
            _FakeQQ.token_requests += 1
            payload = dict(_FakeQQ.token_payload)
            # 令牌支持按 clientSecret 变化（用来验证"换了 secret 会重新取"）
            if _FakeQQ.token_payload.get("echo_secret"):
                payload["access_token"] = f"T-{body.get('clientSecret')}"
                payload.pop("echo_secret", None)
            self._respond(_FakeQQ.token_status, payload)
            return
        _FakeQQ.messages.append(body)
        _FakeQQ.headers.append(dict(self.headers))
        if _FakeQQ.raw_body is not None:
            self.send_response(_FakeQQ.message_status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(_FakeQQ.raw_body)))
            self.end_headers()
            self.wfile.write(_FakeQQ.raw_body)
            return
        self._respond(_FakeQQ.message_status, _FakeQQ.message_payload)

    def _respond(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args) -> None:  # 静音
        return


@pytest.fixture
def qq_server():
    _FakeQQ.token_requests = 0
    _FakeQQ.messages = []
    _FakeQQ.headers = []
    _FakeQQ.token_payload = {"access_token": "T1", "expires_in": 7200}
    _FakeQQ.token_status = 200
    _FakeQQ.message_payload = {"id": "MSG1"}
    _FakeQQ.message_status = 200
    _FakeQQ.raw_body = None
    server = HTTPServer(("127.0.0.1", 0), _FakeQQ)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def make_client(base: str, **kwargs) -> QQChannelClient:
    return QQChannelClient(
        "APPID",
        "SECRET",
        "CHANNEL1",
        api_base=base,
        **kwargs,
    )


# ---------------------------------------------------------------- 配置自检
def test_missing_fields_are_reported() -> None:
    client = QQChannelClient("", "", "")
    assert client.configured is False
    assert client.missing_fields() == ["qq.app_id", "qq.client_secret", "qq.channel_id"]


def test_send_without_config_raises() -> None:
    with pytest.raises(QQBotError) as excinfo:
        QQChannelClient("", "", "").send_text("hi")
    assert "配置不完整" in str(excinfo.value)


def test_empty_text_is_rejected(qq_server) -> None:
    with pytest.raises(QQBotError):
        make_client(qq_server).send_text("   ")


# ---------------------------------------------------------------- 正常链路
def test_send_text_uses_token_and_channel(qq_server) -> None:
    client = make_client(qq_server)
    client.send_text("服务器有人加入了")

    assert _FakeQQ.messages == [{"content": "服务器有人加入了"}]
    assert _FakeQQ.headers[0]["Authorization"] == "QQBot T1"
    assert _FakeQQ.token_requests == 1


def test_token_is_cached_between_sends(qq_server) -> None:
    client = make_client(qq_server)
    client.send_text("第一条")
    client.send_text("第二条")
    assert len(_FakeQQ.messages) == 2
    assert _FakeQQ.token_requests == 1, "token 应该被缓存，而不是每条消息都换一次"


def test_expired_token_triggering_401_is_retried(qq_server) -> None:
    """官方约定 token 过期会返回 11243 之类的错误码，客户端要换一次 token 重试。"""
    _FakeQQ.message_payload = {"err_code": 11243, "message": "token 校验未通过"}

    client = make_client(qq_server)
    # 第一次发消息必然失败（服务端固定返回 11243），断言它重试过、且错误可读
    with pytest.raises(QQBotError) as excinfo:
        client.send_text("hi")
    assert "11243" in str(excinfo.value)
    assert len(_FakeQQ.messages) == 2, "应当用新 token 重试一次"
    assert _FakeQQ.token_requests >= 2


def test_accepted_audit_code_counts_as_success(qq_server) -> None:
    """304023 = 已受理、等待人工审核，这对调用方就是成功。"""
    _FakeQQ.message_payload = {"err_code": 304023, "message": "waiting for audit"}
    body = make_client(qq_server).send_text("hi")
    assert body["err_code"] == 304023


def test_rate_limit_error_is_reported(qq_server) -> None:
    _FakeQQ.message_payload = {"err_code": 304045, "message": "子频道主动消息数限频"}
    with pytest.raises(QQBotError) as excinfo:
        make_client(qq_server).send_text("hi")
    message = str(excinfo.value)
    assert "304045" in message and "限频" in message


def test_long_text_is_truncated(qq_server) -> None:
    make_client(qq_server).send_text("x" * 5000)
    assert len(_FakeQQ.messages[0]["content"]) <= 3801


# ---------------------------------------------------------------- 错误信封
def test_token_endpoint_error_in_200_body(qq_server) -> None:
    """换 token 失败时 HTTP 仍是 200，错误在 body.code 里。"""
    _FakeQQ.token_payload = {"code": 100016, "message": "invalid appid or secret"}
    with pytest.raises(QQBotError) as excinfo:
        make_client(qq_server).send_text("hi")
    assert "100016" in str(excinfo.value)
    assert "invalid appid or secret" in str(excinfo.value)


def test_http_error_body_is_surfaced(qq_server) -> None:
    _FakeQQ.message_status = 429
    _FakeQQ.message_payload = {"err_code": 20028, "message": "ChannelHitWriteRateLimit"}
    with pytest.raises(QQBotError) as excinfo:
        make_client(qq_server).send_text("hi")
    assert "20028" in str(excinfo.value)


def test_non_json_response_is_reported(qq_server) -> None:
    """响应不是 JSON（例如被网关/防护页拦截）时要给出可读错误，而不是崩掉。"""
    _FakeQQ.raw_body = b"<html>502 Bad Gateway</html>"
    try:
        with pytest.raises(QQBotError) as excinfo:
            make_client(qq_server).send_text("hi")
        assert "不是 JSON" in str(excinfo.value)
    finally:
        _FakeQQ.raw_body = None


def test_network_failure_becomes_qqbot_error() -> None:
    # 127.0.0.1:9 通常直接拒绝连接
    client = QQChannelClient("APPID", "SECRET", "CH1", api_base="http://127.0.0.1:9")
    with pytest.raises(QQBotError):
        client.send_text("hi")


# ---------------------------------------------------------------- 失效重试的两种信封
def test_code_only_envelope_is_not_treated_as_success(qq_server) -> None:
    """消息接口只回了 `code`（没有 `err_code`）时，11243 仍要触发换 token 重试。"""
    _FakeQQ.message_payload = {"code": 11243, "message": "token 校验未通过"}

    with pytest.raises(QQBotError) as excinfo:
        make_client(qq_server).send_text("hi")
    assert "11243" in str(excinfo.value)
    assert len(_FakeQQ.messages) == 2, "只认 err_code 会把这种信封当成功"
    assert _FakeQQ.token_requests >= 2


def test_http_401_triggers_a_token_refresh(qq_server) -> None:
    """HTTP 401/403 也是令牌失效；原先它在 _post_json 里就抛了，重试逻辑根本走不到。"""
    _FakeQQ.message_status = 401
    _FakeQQ.message_payload = {"code": 11243, "message": "token 校验未通过"}

    with pytest.raises(QQBotError):
        make_client(qq_server).send_text("hi")
    assert len(_FakeQQ.messages) == 2, "HTTP 401 也应当换一次 token 重试"
    assert _FakeQQ.token_requests >= 2


def test_short_lived_token_is_still_cached(qq_server) -> None:
    """`expires_in` 只有 60s 时，不能用固定 60s 的提前量把每个请求都变成换 token。"""
    _FakeQQ.token_payload = {"access_token": "T1", "expires_in": 60}
    client = make_client(qq_server)
    client.send_text("第一条")
    client.send_text("第二条")
    assert _FakeQQ.token_requests == 1


# ---------------------------------------------------------------- 与 Notifier 的整合
def test_notifier_delivers_through_qq(qq_server, settings) -> None:
    from app.services.notifications import NotificationConfig, Notifier

    notifier = Notifier(
        config=NotificationConfig(
            provider="qq",
            qq_app_id="APPID",
            qq_client_secret="SECRET",
            qq_channel_id="CHANNEL1",
            qq_api_base=qq_server,
        )
    )
    assert notifier.enabled is True
    assert notifier.effective_format == "qq"

    result = notifier.test()
    assert result["ok"] is True, result
    assert _FakeQQ.messages and "测试" in _FakeQQ.messages[0]["content"]
    assert notifier.status()["deliveries"][0]["ok"] is True


def test_notifier_reports_qq_failure(settings) -> None:
    from app.services.notifications import NotificationConfig, Notifier

    notifier = Notifier(
        config=NotificationConfig(
            provider="qq",
            qq_app_id="APPID",
            qq_client_secret="SECRET",
            qq_channel_id="CHANNEL1",
            qq_api_base="http://127.0.0.1:9",
        )
    )
    result = notifier.test()
    assert result["ok"] is False
    assert result["error"]


def test_notifier_missing_qq_config_is_disabled() -> None:
    from app.services.notifications import NotificationConfig, Notifier

    notifier = Notifier(config=NotificationConfig(provider="qq", qq_app_id="APPID"))
    assert notifier.enabled is False
    assert notifier.status()["missing"] == ["qq.client_secret", "qq.channel_id"]
    assert notifier.test()["ok"] is False
