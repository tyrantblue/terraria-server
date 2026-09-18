"""通知配置的持久化：环境变量给默认值，`control/notify.json` 覆盖。

为什么要落盘：环境变量只能改一次重启一次容器，而「把飞书换成 QQ 频道机器人」这种事
应该是面板里点一下就生效。所以：

* 启动时读 `control/notify.json`；文件不存在就用 `NOTIFY_*` 环境变量（`source=env`）。
* API 写入时原子替换该文件，并立刻 `Notifier.configure()`（不重启、不丢投递记录）。
* `DELETE` 就是删掉文件，回到环境变量那套默认值。

合并语义（面板「读出来 → 改一改 → 写回去」的流程要能安全工作）：

| 请求里 | 结果 |
| --- | --- |
| 字段省略 / `null` | 保持原值 |
| `""` | 清空（`provider` 例外：传空等于保持；`events` 传空 = 全部事件） |
| 掩码（`••••••` / `https://host/…`） | 保持原值（掩码不可能是真实凭据） |
| 其它 | 设为该值 |

文件损坏时**不会**让 API 起不来：记一条 warning，退回环境变量默认值。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from app.core.errors import BadRequest
from app.core.masking import resolve_secret
from app.services.notifications import (
    KNOWN_EVENTS,
    PROVIDERS,
    NotificationConfig,
    config_from_env,
)

logger = logging.getLogger(__name__)

#: 落盘格式版本（将来改结构时用来做迁移）
SCHEMA_VERSION = 1


def check_http_url(value: str, *, field: str) -> None:
    """确认是能解析的 http(s) URL。

    必须真的 `urlsplit()` 一次：只看前缀的话，`http://[::1`、全角斜杠这类
    字符串会被放行并**落盘**，之后 `mask_url()`/`webhook_style()` 抛 ValueError——
    通知接口 500，重启时 API 甚至起不来（`Notifier.start()`）。
    """
    text = (value or "").strip()
    if not text:
        raise BadRequest(f"{field} 不能为空", details={field: value})
    try:
        parts = urlsplit(text)
        host = parts.hostname
        parts.port  # 非法端口（如 99999）也会在这里抛
    except ValueError as exc:
        raise BadRequest(
            f"{field} 不是合法 URL：{exc}", details={field: value}
        ) from None
    if parts.scheme not in ("http", "https") or not host:
        raise BadRequest(
            f"{field} 必须是 http:// 或 https:// 开头的完整 URL",
            details={field: value},
        )


class NotificationSettings:
    def __init__(self, path: Path, *, defaults: NotificationConfig) -> None:
        self.path = path
        self.defaults = defaults

    # -- 读 -----------------------------------------------------------
    def load(self) -> NotificationConfig:
        if not self.path.exists():
            return self.defaults
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("通知配置读取失败（%s）：%s；先用环境变量默认值", self.path, exc)
            return self.defaults
        if not isinstance(payload, dict):
            logger.warning("通知配置结构异常（%s）；先用环境变量默认值", self.path)
            return self.defaults
        return self._from_payload(payload)

    def _from_payload(self, payload: dict) -> NotificationConfig:
        qq = payload.get("qq") if isinstance(payload.get("qq"), dict) else {}
        return NotificationConfig(
            provider=str(payload.get("provider") or "auto"),
            url=str(payload.get("url") or ""),
            events=str(payload.get("events") or ""),
            qq_app_id=str(qq.get("app_id") or ""),
            qq_client_secret=str(qq.get("client_secret") or ""),
            qq_channel_id=str(qq.get("channel_id") or ""),
            qq_sandbox=bool(qq.get("sandbox")),
            qq_api_base=str(qq.get("api_base") or ""),
            qq_token_url=str(qq.get("token_url") or ""),
            source="file",
        )

    def _to_payload(self, config: NotificationConfig) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "provider": config.provider_name,
            "url": config.url,
            "events": config.events,
            "qq": {
                "app_id": config.qq_app_id,
                "client_secret": config.qq_client_secret,
                "channel_id": config.qq_channel_id,
                "sandbox": config.qq_sandbox,
                "api_base": config.qq_api_base,
                "token_url": config.qq_token_url,
            },
            "updated_at": time.time(),
        }

    # -- 写 -----------------------------------------------------------
    def merge(self, current: NotificationConfig, payload: dict) -> NotificationConfig:
        """把请求体（可能只带了一部分字段）合并到当前配置上。"""
        qq_in = payload.get("qq") if isinstance(payload.get("qq"), dict) else {}

        provider = payload.get("provider")
        url = payload.get("url")
        events = payload.get("events")

        def _pick(incoming: object, existing: str) -> str:
            if incoming is None:
                return existing
            return str(incoming).strip()

        def _pick_secret(incoming: object, existing: str) -> str:
            """`resolve_secret()` 用 None 表示「保持不变」，这里要还原成原值。"""
            resolved = resolve_secret(incoming, existing)
            return existing if resolved is None else resolved

        return NotificationConfig(
            # `provider` 没有「清空」语义：传空等于保持（`auto` 是显式值，不是空）
            provider=_pick(provider, current.provider_name) or current.provider_name,
            url=_pick_secret(url, current.url),
            events=_pick(events, current.events),
            qq_app_id=_pick(qq_in.get("app_id"), current.qq_app_id),
            qq_client_secret=_pick_secret(qq_in.get("client_secret"), current.qq_client_secret),
            qq_channel_id=_pick(qq_in.get("channel_id"), current.qq_channel_id),
            qq_sandbox=(
                bool(qq_in.get("sandbox"))
                if qq_in.get("sandbox") is not None
                else current.qq_sandbox
            ),
            # 视图里这两个也是掩码（自建网关可能内嵌 basic-auth），掩码=保持
            qq_api_base=_pick_secret(qq_in.get("api_base"), current.qq_api_base),
            qq_token_url=_pick_secret(qq_in.get("token_url"), current.qq_token_url),
            source="file",
        )

    def validate(self, config: NotificationConfig) -> None:
        """业务校验：渠道合法、必填齐全、事件名认得出来。

        `provider=none` 是「明确关闭」，不需要任何传输配置；其余渠道**要求配置完整**
        ——否则面板会陷入「保存成功但什么都不发」的状态。要清空 QQ 凭据请改用
        `none`（或 DELETE 回到环境变量默认值），而不是把 secret 置空留在 qq 渠道上。
        """
        if config.provider_name not in PROVIDERS:
            raise BadRequest(
                f"不支持的通知渠道：{config.provider_name}",
                details={"allowed": list(PROVIDERS)},
            )

        # 「高级覆盖」是 URL，同样必须能解析：写坏了会落到 mask_url()/webhook_style()
        # 抛 ValueError 的那条路上（见 check_http_url 的说明）。
        for field, value in (
            ("qq.api_base", config.qq_api_base),
            ("qq.token_url", config.qq_token_url),
        ):
            if (value or "").strip():
                check_http_url(value, field=field)

        if config.is_off:
            missing: list[str] = []
        elif config.is_qq:
            missing = config.qq_client().missing_fields()
            if missing:
                raise BadRequest(
                    f"QQ 频道机器人缺少配置：{', '.join(missing)}",
                    details={"missing": missing},
                )
        else:
            if not config.url.strip():
                raise BadRequest(
                    f"{config.provider_name} 需要 webhook URL",
                    details={"missing": ["url"]},
                )
            check_http_url(config.url, field="url")

        events = [item.strip() for item in config.events.split(",") if item.strip()]
        unknown = [item for item in events if item not in KNOWN_EVENTS]
        if unknown:
            raise BadRequest(
                f"未知的事件名：{', '.join(unknown)}",
                details={"unknown": unknown, "allowed": list(KNOWN_EVENTS)},
            )

    def save(self, current: NotificationConfig, payload: dict) -> NotificationConfig:
        config = self.merge(current, payload)
        self.validate(config)
        self._warn_if_secret_follows_new_host(current, config)
        self._write(config)
        return config

    def _warn_if_secret_follows_new_host(
        self, current: NotificationConfig, config: NotificationConfig
    ) -> None:
        """改了 token_url / api_base 却沿用旧 client_secret 时留一条日志。

        能做这次写入的调用方，本来就能把 webhook 指到自己的服务器上，所以这里
        不做「强制重填 secret」的硬拦（那会让只改 sandbox 也变得很麻烦），
        只保证「密钥被发往了新域名」这件事在日志里看得见。
        """
        moved = (
            config.qq_token_url != current.qq_token_url
            or config.qq_api_base != current.qq_api_base
        )
        if (
            moved
            and config.qq_client_secret
            and config.qq_client_secret == current.qq_client_secret
        ):
            logger.warning(
                "通知配置：QQ 的 api_base/token_url 已变更（%s → %s），但仍沿用原有 "
                "client_secret；请确认新域名可信",
                current.qq_api_base or current.qq_token_url or "默认域名",
                config.qq_api_base or config.qq_token_url or "默认域名",
            )

    def _write(self, config: NotificationConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._to_payload(config), ensure_ascii=False, indent=2) + "\n"
        # 唯一临时名（并发 PUT 不再共用同一个 .tmp）+ 以 0600 创建（umask 只会更严），
        # 也就是**在 rename 之前**就已经是最终权限。这个文件里有 webhook URL 与
        # client_secret：旧写法 rename 之后才 chmod，那段时间里它是可读的，
        # 进程若在窗口内退出就会永久停在 0644。
        temp = self.path.with_name(f"{self.path.name}.{uuid4().hex[:8]}.tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    fd = -1  # 所有权已交给 fdopen
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                if fd >= 0:  # pragma: no cover - fdopen 自身失败
                    os.close(fd)
            os.replace(temp, self.path)
        finally:
            # 任何失败都不要留下半个（同样含密钥的）临时文件
            temp.unlink(missing_ok=True)

    def reset(self) -> NotificationConfig:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise BadRequest(f"删除通知配置失败：{exc}") from exc
        return self.defaults


def build_settings(path: Path, settings) -> NotificationSettings:
    return NotificationSettings(path, defaults=config_from_env(settings))
