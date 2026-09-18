"""issue #13：上传预检的 413/507 响应必须带 CORS 头。

中间件在 CORSMiddleware 外层早退时，浏览器会把它当成跨域失败，面板只能报
「连接中断」。修复方式是调整中间件顺序，让 CORSMiddleware 包住所有早退响应，
并让 `X-Client-Version` 回显也在最外层。
"""

from __future__ import annotations

import dataclasses

from fastapi.testclient import TestClient

from app.main import create_app
from app.services import world_service
from app.services.runtime import build_runtime

ORIGIN = {"Origin": "https://panel.example.com"}


def _client(settings, *, max_bytes: int):
    tiny = dataclasses.replace(settings, world_upload_max_bytes=max_bytes)
    return TestClient(create_app(build_runtime(tiny)))


def test_oversize_413_has_cors_and_version_echo(settings, fake_terraria) -> None:
    with _client(settings, max_bytes=1024) as client:
        response = client.post(
            "/api/v1/worlds",
            files={"file": ("big.wld", b"W" * 4096, "application/octet-stream")},
            headers={**ORIGIN, "X-Client-Version": "1.4.1"},
        )
        assert response.status_code == 413
        assert response.headers["access-control-allow-origin"] == "*"
        assert response.headers["x-client-version"] == "1.4.1"
        body = response.json()
        assert body["error"]["code"] == "payload_too_large"
        assert body["error"]["details"]["limit_bytes"] == 1024


def test_low_disk_507_has_cors_headers(settings, fake_terraria, monkeypatch) -> None:
    monkeypatch.setattr(world_service, "free_bytes", lambda _path: 4096)
    with _client(settings, max_bytes=10 * 1024 * 1024) as client:
        response = client.post(
            "/api/v1/worlds",
            files={"file": ("full.wld", b"W" * 1024, "application/octet-stream")},
            headers=ORIGIN,
        )
        assert response.status_code == 507
        assert response.headers["access-control-allow-origin"] == "*"
        body = response.json()
        assert body["error"]["code"] == "insufficient_storage"
        assert body["error"]["details"]["free_bytes"] == 4096


def test_cors_preflight_is_answered(client) -> None:
    response = client.options(
        "/api/v1/worlds",
        headers={
            **ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-client-version",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_chunked_upload_service_path_also_has_cors(settings, fake_terraria) -> None:
    """没有 Content-Length 时中间件不预检，413 由 service 层抛出——同样带 CORS 头。

    这条覆盖 issue #13 验收标准里的「两条路径（中间件预检 / service 层）行为一致」。
    """
    boundary = "----terrariatest"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="chunked.wld"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + b"W" * 4096 + f"\r\n--{boundary}--\r\n".encode()

    def stream():
        yield body

    with _client(settings, max_bytes=1024) as client:
        response = client.post(
            "/api/v1/worlds",
            # 生成器内容 → httpx 用 chunked，不带 Content-Length，中间件因此放行
            content=stream(),
            headers={
                **ORIGIN,
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        assert "content-length" not in response.request.headers
        assert response.status_code == 413
        assert response.headers["access-control-allow-origin"] == "*"
        body_json = response.json()
        assert body_json["error"]["code"] == "payload_too_large"
        assert "limit_bytes" in body_json["error"]["details"]
