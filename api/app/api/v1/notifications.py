"""v1：事件通知（目标配置、状态查看与测试发送）。

配置分两层（见 services/notification_settings.py）：

* `NOTIFY_*` 环境变量给默认值；
* `control/notify.json` 是运行时配置，**覆盖**环境变量，改完立刻生效、不用重启容器。

支持的渠道：`feishu` / `discord` / `slack` / 通用 `json`（都是 POST 一个 webhook URL），
以及 `qq`（QQ 频道机器人，用 appId + clientSecret 换 token 后往子频道发消息）。
"""

from __future__ import annotations

import threading

from fastapi import APIRouter

from app.api.deps import RuntimeDep
from app.schemas.v1 import NotificationSettingsUpdate, NotificationStatus

router = APIRouter(prefix="/api/v1", tags=["v1:notifications"])

#: 读（notifier.config）→ 合并 → 落盘 必须整体串行：路由是同步函数，
#: FastAPI 会在线程池里并发执行，双击保存/面板重试会让两次写互相覆盖。
_settings_lock = threading.Lock()


@router.get("/notifications", response_model=NotificationStatus)
def get_notifications(rt: RuntimeDep) -> dict[str, object]:
    """当前生效的通知配置 + 最近投递记录（敏感字段已掩码）。"""
    return rt.notifier.status()


@router.put("/notifications/settings", response_model=NotificationStatus)
def update_notification_settings(
    request: NotificationSettingsUpdate,
    rt: RuntimeDep,
) -> dict[str, object]:
    """改通知目标并立即生效（落盘 `control/notify.json`）。

    * `provider`：`auto` | `feishu` | `discord` | `slack` | `json` | `qq` | `none`；
    * webhook 类渠道需要 `url`；`qq` 需要 `qq.app_id` + `qq.client_secret` + `qq.channel_id`；
    * `events` 是逗号分隔白名单（空 = 全部），事件名写错会 **400** 而不是静默过滤；
    * 只改带来的字段：省略/`null` 保持原值，`""` 清空（`provider` 例外，传空等于保持），
      掩码表示"不改"；URL 不合法一律 **400**，不会落盘。
    """
    payload = request.model_dump(exclude_unset=True)
    with _settings_lock:
        config = rt.notify_settings.save(rt.notifier.config, payload)
        rt.notifier.configure(config)
    return rt.notifier.status()


@router.delete("/notifications/settings", response_model=NotificationStatus)
def reset_notification_settings(rt: RuntimeDep) -> dict[str, object]:
    """删掉运行时配置，回到 `NOTIFY_*` 环境变量的默认值。"""
    with _settings_lock:
        config = rt.notify_settings.reset()
        rt.notifier.configure(config)
    return rt.notifier.status()


@router.post("/notifications/test", response_model=dict)
def test_notification(rt: RuntimeDep) -> dict[str, object]:
    """同步发一条测试消息，配置完可以立刻验证（失败原因会写在 `error` 里）。"""
    return rt.notifier.test()
