"""事件通知（webhook）。

支持 Discord / Slack / 通用 JSON 三种载荷。用标准库 urllib + 单个后台线程发送，
不阻塞请求也不阻塞调度线程；投递结果保留最近若干条，面板可以自查。

配置（环境变量，见 core/settings.py）：

    NOTIFY_WEBHOOK_URL=https://discord.com/api/webhooks/...
    NOTIFY_FORMAT=auto|discord|slack|json      # auto 按 URL 猜
    NOTIFY_EVENTS=player_join,player_leave,... # 空 = 全部
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

LEVEL_ICON = {
    "info": "ℹ️",
    "success": "✅",
    "warning": "⚠️",
    "error": "❌",
}


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


def mask_url(url: str) -> str:
    """只露出主机名——webhook URL 本身就是凭据，不能原样回给前端。"""
    if not url:
        return ""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/…" if parts.netloc else "…"


class Notifier:
    def __init__(
        self,
        url: str = "",
        *,
        fmt: str = "auto",
        events: str = "",
        capacity: int = 50,
        timeout: float = 5.0,
    ) -> None:
        self.url = url.strip()
        self.fmt = (fmt or "auto").strip().lower()
        self.timeout = timeout
        self.events = {item.strip() for item in events.split(",") if item.strip()}
        self._queue: queue.Queue[tuple[str, str, dict, str]] = queue.Queue(maxsize=200)
        self._deliveries: list[Delivery] = []
        self._lock = threading.Lock()
        self._capacity = capacity
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- 生命周期 -----------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def start(self) -> None:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="notifier", daemon=True)
        self._thread.start()
        logger.info("notifier: 已启用（%s）", mask_url(self.url))

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    # -- 对外 ---------------------------------------------------------
    def status(self) -> dict[str, object]:
        with self._lock:
            deliveries = [item.as_dict() for item in self._deliveries]
        return {
            "enabled": self.enabled,
            "url": mask_url(self.url),
            "format": self.fmt,
            "events": sorted(self.events) if self.events else "all",
            "deliveries": deliveries,
        }

    def notify(self, event: str, title: str, *, level: str = "info", detail: dict | None = None) -> None:
        if not self.enabled:
            return
        if self.events and event not in self.events:
            return
        try:
            self._queue.put_nowait((event, title, detail or {}, level))
        except queue.Full:  # pragma: no cover - 极端情况
            logger.warning("notifier: 队列已满，丢弃事件 %s", event)

    def test(self) -> dict[str, object]:
        """同步发一条测试消息，方便配置完立刻验证。"""
        if not self.enabled:
            return {"ok": False, "error": "未配置 NOTIFY_WEBHOOK_URL"}
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
        payload = self._payload(event, title, detail, level)
        data = json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(
            self.url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "terraria-api/1.0"},
        )
        delivery = Delivery(ts=time.time(), event=event, title=title, ok=False)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                delivery.ok = 200 <= response.status < 300
                delivery.status = response.status
        except urllib.error.HTTPError as exc:
            delivery.status = exc.code
            delivery.error = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - 通知失败不能影响主流程
            delivery.error = str(exc)
        if not delivery.ok:
            logger.warning("notifier: 投递 %s 失败: %s", event, delivery.error or delivery.status)
        with self._lock:
            self._deliveries.insert(0, delivery)
            del self._deliveries[self._capacity :]
        return delivery

    def _payload(self, event: str, title: str, detail: dict, level: str) -> dict:
        icon = LEVEL_ICON.get(level, "")
        text = f"{icon} {title}".strip()
        if detail:
            extra = " ".join(f"{k}={v}" for k, v in detail.items() if v not in (None, ""))
            if extra:
                text += f"\n{extra}"

        fmt = self.fmt
        if fmt == "auto":
            host = urlsplit(self.url).netloc
            fmt = "discord" if "discord" in host else "slack" if "slack" in host else "json"

        if fmt == "discord":
            return {"content": text[:1900]}
        if fmt == "slack":
            return {"text": text[:3000]}
        return {
            "event": event,
            "level": level,
            "title": title,
            "detail": detail,
            "ts": time.time(),
        }
