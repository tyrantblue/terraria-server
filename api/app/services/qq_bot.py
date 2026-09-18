"""QQ 频道机器人（官方开放平台）客户端。

为什么单独一个模块：飞书/Discord/Slack 都是「往一个 webhook URL POST 一段 JSON」，
而 QQ 频道机器人API 是「用 appId + clientSecret 换 access_token，再用 token 往指定
子频道发消息」，鉴权、令牌刷新、错误码都不是同一套东西，硬塞进 webhook 分支只会更乱。

官方文档（2026-09 实测口径）：

* 换取凭证：`POST https://api.bot.qq.com/app/getAppAccessToken`
  body `{"appId", "clientSecret"}` → `{"access_token", "expires_in"}`。
  **失败时 HTTP 仍然是 200**，错误在响应体的 `code` 里（和飞书一样坑）。
* 调用接口：`Authorization: QQBot {ACCESS_TOKEN}`，统一域名 `https://api.bot.qq.com`。
* 发子频道消息：`POST /channels/{channel_id}/messages`，body `{"content": "..."}`；
  失败时响应体里是 `err_code`（成功为 0）。

三个必须知道的限制（都写进了 docs/api/v1.md）：

1. 主动消息默认**每个子频道每天 20 条**，每个频道每天最多推 2 个子频道，单频道 1s/5 条；
   撞上限会返回 `304045` / `304035` 之类的错误码。
2. 发消息要求机器人**有 websocket 连接到 gateway**，否则 `304018 SESSION_NOT_EXIST`。
   也就是说纯 HTTP 推送并不保证送达——API 这边只能如实把错误记进投递记录。
3. `304023` / `304024` 表示「已受理，等待人工审核」，算成功。

需要标准库以外的依赖吗？不需要：令牌与发消息都是普通 HTTPS JSON 请求，
`urllib.request` 足够，和 `notifications.py` 的 webhook 保持同一套做法。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

#: 统一请求地址（发送子频道消息也在这个域名下）
DEFAULT_API_BASE = "https://api.bot.qq.com"
#: 换取 access_token 的地址
DEFAULT_TOKEN_URL = f"{DEFAULT_API_BASE}/app/getAppAccessToken"
#: 沙箱环境（历史文档口径；如果 QQ 调整了域名，用配置里的 api_base/token_url 覆盖）
SANDBOX_API_BASE = "https://sandbox.api.sgroup.qq.com"
SANDBOX_TOKEN_URL = f"{SANDBOX_API_BASE}/app/getAppAccessToken"

#: access_token 提前多少秒视为「该换了」（官方建议过期前 60s 内会拿到新值）
TOKEN_REFRESH_MARGIN = 60.0
#: 单条消息的长度上限（QQ 频道 content 限制约 4000 字符，这里留点余量）
MAX_CONTENT_CHARS = 3800
#: 「已受理，等待审核」也算成功
ACCEPTED_AUDIT_CODES = {304023, 304024}


class QQBotError(Exception):
    """QQ 开放平台返回的业务错误（或网络层错误）。"""


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class QQChannelClient:
    """一个 appId/clientSecret 对应一个客户端；token 在实例内缓存。"""

    def __init__(
        self,
        app_id: str,
        client_secret: str,
        channel_id: str,
        *,
        sandbox: bool = False,
        api_base: str = "",
        token_url: str = "",
        timeout: float = 5.0,
    ) -> None:
        self.app_id = (app_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.channel_id = (channel_id or "").strip()
        self.timeout = timeout
        self.api_base = (api_base or (SANDBOX_API_BASE if sandbox else DEFAULT_API_BASE)).rstrip("/")
        self.token_url = token_url or (
            SANDBOX_TOKEN_URL if sandbox and not api_base else f"{self.api_base}/app/getAppAccessToken"
        )
        self._lock = threading.Lock()
        self._token: str | None = None
        self._token_expires_at = 0.0

    # -- 配置自检 -----------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.client_secret and self.channel_id)

    def missing_fields(self) -> list[str]:
        missing = []
        if not self.app_id:
            missing.append("qq.app_id")
        if not self.client_secret:
            missing.append("qq.client_secret")
        if not self.channel_id:
            missing.append("qq.channel_id")
        return missing

    # -- 令牌 ---------------------------------------------------------
    def token(self, *, force: bool = False) -> str:
        """取 access_token，带缓存；临近过期（60s）会自动换新的。"""
        with self._lock:
            if (
                not force
                and self._token
                and time.time() < self._token_expires_at - TOKEN_REFRESH_MARGIN
            ):
                return self._token

        payload = {"appId": self.app_id, "clientSecret": self.client_secret}
        body = self._post_json(self.token_url, payload, headers={})
        # 文档：失败时 HTTP 仍是 200，错误码在 body.code
        code = _as_int(body.get("code"))
        token = str(body.get("access_token") or "")
        if code not in (0, None) or not token:
            raise QQBotError(
                f"取 access_token 失败：code={code if code is not None else '?'} "
                f"{body.get('message') or body.get('msg') or ''}".strip()
            )
        expires = _as_int(body.get("expires_in")) or 7200
        with self._lock:
            self._token = token
            self._token_expires_at = time.time() + max(60, expires)
        return token

    # -- 发送 ---------------------------------------------------------
    def send_text(self, text: str) -> dict[str, object]:
        """往配置的子频道发一条主动文本消息，返回响应体。"""
        if not self.configured:
            raise QQBotError(f"QQ 配置不完整，缺少：{', '.join(self.missing_fields())}")

        content = (text or "").strip()
        if not content:
            raise QQBotError("消息内容为空")
        if len(content) > MAX_CONTENT_CHARS:
            content = content[:MAX_CONTENT_CHARS] + "…"

        url = f"{self.api_base}/channels/{self.channel_id}/messages"
        token = self.token()
        body = self._post_json(
            url,
            {"content": content},
            headers={"Authorization": f"QQBot {token}"},
        )

        err_code = _as_int(body.get("err_code"))
        if err_code in (None, 0):
            return body
        if err_code in ACCEPTED_AUDIT_CODES:
            logger.info("qq: 消息已受理，等待平台审核（err_code=%s）", err_code)
            return body
        # 令牌失效（401 / 11243 等）：换一次 token 重试一次
        if err_code in (11241, 11242, 11243) or _as_int(body.get("code")) in (11241, 11242, 11243):
            token = self.token(force=True)
            body = self._post_json(
                url,
                {"content": content},
                headers={"Authorization": f"QQBot {token}"},
            )
            err_code = _as_int(body.get("err_code"))
            if err_code in (None, 0) or err_code in ACCEPTED_AUDIT_CODES:
                return body
        raise QQBotError(
            f"发送失败：err_code={err_code} {body.get('message') or ''}".strip()
        )

    # -- HTTP ---------------------------------------------------------
    def _post_json(
        self, url: str, payload: dict[str, object], *, headers: dict[str, str]
    ) -> dict[str, object]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "terraria-api/1.0",
                **headers,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            # 401/429/5xx：尽量把响应体里的错误码带出来
            detail = _extract_error(raw) or f"HTTP {exc.code}"
            raise QQBotError(detail) from exc
        except Exception as exc:  # noqa: BLE001 - 网络错误统一成 QQBotError
            raise QQBotError(f"{type(exc).__name__}: {exc}") from exc

        try:
            body = json.loads(raw) if raw else {}
        except ValueError as exc:
            raise QQBotError(f"响应不是 JSON（HTTP {status}）") from exc
        if not isinstance(body, dict):
            raise QQBotError(f"响应结构异常（HTTP {status}）")
        return body


def _extract_error(raw: str) -> str | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return raw[:200]
    if not isinstance(payload, dict):
        return raw[:200]
    code = payload.get("err_code", payload.get("code"))
    message = payload.get("message") or payload.get("msg") or ""
    if code is None and not message:
        return None
    return f"err_code={code} {message}".strip()
