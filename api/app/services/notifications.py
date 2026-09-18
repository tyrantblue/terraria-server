"""事件通知：webhook（Discord / Slack / 飞书 / 通用 JSON）与 QQ 频道机器人。

用标准库 urllib + 单个后台线程发送，不阻塞请求也不阻塞调度线程；投递结果保留最近
若干条，面板可以自查。**QQ 频道机器人**走另一条链路（appId+clientSecret 换 token），
细节见 services/qq_bot.py。

配置来源有两层（见 services/notification_settings.py）：

1. 环境变量（`NOTIFY_*`，docker-compose 里给默认值）；
2. `control/notify.json`（面板/API 写的运行时配置，**覆盖**环境变量）。

`Notifier` 支持运行时重配（`configure()`）：改完不用重启容器，也不丢投递记录。

飞书与 QQ 的坑：它们的 HTTP 状态码在失败时也可能是 200，真正的结果在响应体里，
所以两条链路都会额外解析响应体，避免"看起来投递成功其实没发出去"。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from app.core.masking import mask_secret, mask_url, secret_is_set
from app.services.qq_bot import QQBotError, QQChannelClient

logger = logging.getLogger(__name__)

#: 兼容旧调用：`from app.services.notifications import mask_url`
__all__ = [
    "Delivery",
    "KNOWN_EVENTS",
    "NotificationConfig",
    "Notifier",
    "mask_secret",
    "mask_url",
    "secret_is_set",
]

LEVEL_ICON = {
    "info": "ℹ️",
    "success": "✅",
    "warning": "⚠️",
    "error": "❌",
}

#: 通知渠道。"" / auto = 按 URL 猜；qq = 频道机器人（不用 URL）；none = 明确关闭
WEBHOOK_PROVIDERS = ("auto", "discord", "slack", "feishu", "json")
PROVIDERS = ("none",) + WEBHOOK_PROVIDERS + ("qq",)

#: 目前会产生的事件（通知白名单用它校验，打错字不会被静默丢掉）
KNOWN_EVENTS = (
    "player_join",
    "player_leave",
    "player_booted",
    "server_up",
    "server_error",
    "backup_done",
    "schedule_failed",
    "restart_skipped",
    "log_stalled",
    "test",
)


@dataclass
class Delivery:
    ts: float
    event: str
    title: str
    ok: bool
    status: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts,
            "event": self.event,
            "title": self.title,
            "ok": self.ok,
            "status": self.status,
            "error": self.error,
        }


@dataclass(frozen=True)
class NotificationConfig:
    """一条生效的通知配置（单目标；多目标是后续的事）。"""

    provider: str = "auto"          # auto | discord | slack | feishu | json | qq
    url: str = ""                   # webhook 类渠道用
    events: str = ""                # 逗号分隔白名单；空 = 全部
    qq_app_id: str = ""
    qq_client_secret: str = ""
    qq_channel_id: str = ""
    qq_sandbox: bool = False
    qq_api_base: str = ""           # 高级：覆盖默认域名（沙箱/自建网关/测试）
    qq_token_url: str = ""
    source: str = "env"             # env | file：这份配置从哪来，给面板看

    # -- 派生信息 -----------------------------------------------------
    @property
    def provider_name(self) -> str:
        return (self.provider or "auto").strip().lower()

    @property
    def is_qq(self) -> bool:
        return self.provider_name == "qq"

    @property
    def is_off(self) -> bool:
        """`provider=none`：明确关闭通知（配置留着，只是不投递）。"""
        return self.provider_name == "none"

    def webhook_style(self) -> str:
        """webhook 的载荷风格；auto 时按 URL 猜：discord / slack / feishu / json。"""
        provider = self.provider_name
        if provider not in ("auto",):
            return provider
        host = urlsplit(self.url).netloc.lower()
        if "discord" in host:
            return "discord"
        if "slack" in host:
            return "slack"
        if "feishu" in host or "larksuite" in host or "lark" in host:
            return "feishu"
        return "json"

    def missing(self) -> list[str]:
        """配置不完整时缺哪些字段（面板直接显示，不用猜）。"""
        if self.is_off:
            return []
        if self.is_qq:
            client = self.qq_client()
            return client.missing_fields()
        if not self.url.strip():
            return ["url"]
        return []

    def qq_client(self, *, timeout: float = 5.0) -> QQChannelClient:
        return QQChannelClient(
            self.qq_app_id,
            self.qq_client_secret,
            self.qq_channel_id,
            sandbox=self.qq_sandbox,
            api_base=self.qq_api_base,
            token_url=self.qq_token_url,
            timeout=timeout,
        )

    def as_dict(self, *, masked: bool = True) -> dict[str, object]:
        """给 API 的视图。默认脱敏：URL 只留主机名，secret 只留"是否已设置"。"""
        return {
            "provider": self.provider_name,
            "url": mask_url(self.url) if masked else self.url,
            "url_set": secret_is_set(self.url),
            "events": self.events,
            "source": self.source,
            "qq": {
                "app_id": self.qq_app_id,
                "client_secret": mask_secret(self.qq_client_secret) if masked else self.qq_client_secret,
                "client_secret_set": secret_is_set(self.qq_client_secret),
                "channel_id": self.qq_channel_id,
                "sandbox": self.qq_sandbox,
                "api_base": self.qq_api_base,
                "token_url": self.qq_token_url,
            },
        }


def _check_feishu(body: str) -> tuple[bool, str | None]:
    """飞书永远返回 HTTP 200，真正的结果在响应体的 `code`/`StatusCode` 里。"""
    if not body:
        return True, None
    try:
        payload = json.loads(body)
    except ValueError:
        return True, None
    code = payload.get("code", payload.get("StatusCode", 0))
    if code in (0, None):
        return True, None
    message = payload.get("msg") or payload.get("StatusMessage") or f"feishu code={code}"
    return False, str(message)


class Notifier:
    def __init__(
        self,
        url: str = "",
        *,
        fmt: str = "auto",
        events: str = "",
        config: NotificationConfig | None = None,
        timeout: float = 5.0,
        capacity: int = 50,
    ) -> None:
        # 兼容旧调用：Notifier(url, fmt=..., events=...)
        if config is None:
            config = NotificationConfig(
                provider=(fmt or "auto"),
                url=(url or "").strip(),
                events=events or "",
            )
        self._config = config
        self.timeout = timeout
        self._queue: queue.Queue[tuple[str, str, dict, str]] = queue.Queue(maxsize=200)
        self._deliveries: list[Delivery] = []
        self._lock = threading.Lock()
        self._capacity = capacity
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._qq_client: QQChannelClient | None = None
        self._qq_client_key: tuple[object, ...] | None = None

    # -- 配置 ---------------------------------------------------------
    @property
    def config(self) -> NotificationConfig:
        return self._config

    def configure(self, config: NotificationConfig) -> None:
        """换一份配置（运行时改通知设置走这里），enabled 随之变化。"""
        with self._lock:
            changed = config != self._config
            self._config = config
        if changed:
            logger.info(
                "notifier: 配置已更新（provider=%s, source=%s, enabled=%s）",
                config.provider_name,
                config.source,
                self.enabled,
            )
        # 之前没启用（线程没起）而现在有配置了 → 需要把线程拉起来
        if self.enabled:
            self.start()

    @property
    def enabled(self) -> bool:
        return (not self._config.is_off) and not self._config.missing()

    @property
    def effective_format(self) -> str:
        """实际使用的渠道（qq 或 webhook 风格），API 与文档都用它。"""
        if self._config.is_qq:
            return "qq"
        return self._config.webhook_style()

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="notifier", daemon=True)
        self._thread.start()
        if self._config.is_qq:
            logger.info("notifier: 已启用（QQ 频道机器人，channel=%s）", self._config.qq_channel_id)
        else:
            logger.info("notifier: 已启用（%s）", mask_url(self._config.url))

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    # -- 对外 ---------------------------------------------------------
    def status(self) -> dict[str, object]:
        with self._lock:
            deliveries = [item.as_dict() for item in self._deliveries]
        config = self._config
        return {
            "enabled": self.enabled,
            "provider": config.provider_name,
            "format": self.effective_format,
            "url": mask_url(config.url),
            "url_set": secret_is_set(config.url),
            "events": sorted(config.events.split(",")) if config.events.strip() else "all",
            "missing": config.missing(),
            "source": config.source,
            "qq": {
                "app_id": config.qq_app_id,
                "client_secret": mask_secret(config.qq_client_secret),
                "client_secret_set": secret_is_set(config.qq_client_secret),
                "channel_id": config.qq_channel_id,
                "sandbox": config.qq_sandbox,
                "api_base": config.qq_api_base,
                "token_url": config.qq_token_url,
            },
            "deliveries": deliveries,
        }

    def notify(self, event: str, title: str, *, level: str = "info", detail: dict | None = None) -> None:
        if not self.enabled:
            return
        events = {item.strip() for item in self._config.events.split(",") if item.strip()}
        if events and event not in events:
            return
        try:
            self._queue.put_nowait((event, title, detail or {}, level))
        except queue.Full:  # pragma: no cover - 极端情况
            logger.warning("notifier: 队列已满，丢弃事件 %s", event)

    def test(self) -> dict[str, object]:
        """同步发一条测试消息，方便配置完立刻验证。"""
        if not self.enabled:
            missing = "、".join(self._config.missing()) or "未配置"
            return {"ok": False, "error": f"通知未启用或配置不完整（缺：{missing}）"}
        delivery = self._deliver("test", "Terraria 面板测试消息", {"level": "info"})
        return delivery.as_dict()

    # -- 内部 ---------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                event, title, detail, level = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._deliver(event, title, detail, level)

    def _deliver(self, event: str, title: str, detail: dict, level: str = "info") -> Delivery:
        config = self._config
        if config.is_qq:
            delivery = self._deliver_qq(config, event, title, detail, level)
        else:
            delivery = self._deliver_webhook(config, event, title, detail, level)
        if not delivery.ok:
            logger.warning("notifier: 投递 %s 失败: %s", event, delivery.error or delivery.status)
        with self._lock:
            self._deliveries.insert(0, delivery)
            del self._deliveries[self._capacity :]
        return delivery

    def _deliver_webhook(
        self, config: NotificationConfig, event: str, title: str, detail: dict, level: str
    ) -> Delivery:
        payload = self._payload(event, title, detail, level)
        data = json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(
            config.url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "terraria-api/1.0"},
        )
        delivery = Delivery(ts=time.time(), event=event, title=title, ok=False)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                delivery.status = response.status
                delivery.ok = 200 <= response.status < 300
                if delivery.ok and config.webhook_style() == "feishu":
                    delivery.ok, delivery.error = _check_feishu(body)
        except urllib.error.HTTPError as exc:
            delivery.status = exc.code
            delivery.error = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - 通知失败不能影响主流程
            delivery.error = str(exc)
        return delivery

    def _deliver_qq(
        self, config: NotificationConfig, event: str, title: str, detail: dict, level: str
    ) -> Delivery:
        delivery = Delivery(ts=time.time(), event=event, title=title, ok=False)
        try:
            client = self._shared_qq_client(config)
            client.send_text(self._render_text(event, title, detail, level))
        except QQBotError as exc:
            delivery.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            delivery.error = f"{type(exc).__name__}: {exc}"
        else:
            delivery.status = 200
            delivery.ok = True
        return delivery

    def _shared_qq_client(self, config: NotificationConfig) -> QQChannelClient:
        """复用同一个客户端，token 缓存才有意义。配置变了就换一个。"""
        key = (
            config.qq_app_id,
            config.qq_client_secret,
            config.qq_channel_id,
            config.qq_sandbox,
            config.qq_api_base,
            config.qq_token_url,
        )
        with self._lock:
            if self._qq_client is None or self._qq_client_key != key:
                self._qq_client = config.qq_client(timeout=self.timeout)
                self._qq_client_key = key
            return self._qq_client

    def _render_text(self, event: str, title: str, detail: dict, level: str) -> str:
        icon = LEVEL_ICON.get(level, "")
        text = f"{icon} {title}".strip()
        if detail:
            extra = " ".join(
                f"{key}={value}" for key, value in detail.items() if value not in (None, "")
            )
            if extra:
                text += f"\n{extra}"
        return text

    def _payload(self, event: str, title: str, detail: dict, level: str) -> dict:  # noqa: D102
        text = self._render_text(event, title, detail, level)
        fmt = self.effective_format
        if fmt == "discord":
            return {"content": text[:1900]}
        if fmt == "slack":
            return {"text": text[:3000]}
        if fmt == "feishu":
            # 飞书自定义机器人：纯文本
            return {"msg_type": "text", "content": {"text": text[:4000]}}
        return {
            "event": event,
            "level": level,
            "title": title,
            "detail": detail,
            "ts": time.time(),
        }


def config_from_env(settings) -> NotificationConfig:
    """把 Settings 里的 `NOTIFY_*` 变成一份默认配置（文件不存在时用它）。"""
    return NotificationConfig(
        provider=(settings.notify_format or "auto"),
        url=(settings.notify_webhook_url or "").strip(),
        events=(settings.notify_events or "").strip(),
        qq_app_id=(settings.notify_qq_app_id or "").strip(),
        qq_client_secret=(settings.notify_qq_client_secret or "").strip(),
        qq_channel_id=(settings.notify_qq_channel_id or "").strip(),
        qq_sandbox=bool(settings.notify_qq_sandbox),
        qq_api_base=(settings.notify_qq_api_base or "").strip(),
        qq_token_url=(settings.notify_qq_token_url or "").strip(),
        source="env",
    )


def _with_source(config: NotificationConfig, source: str) -> NotificationConfig:
    return replace(config, source=source)
