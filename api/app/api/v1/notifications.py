"""v1：事件通知（webhook）配置查看与测试。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RuntimeDep
from app.schemas.v1 import NotificationStatus

router = APIRouter(prefix="/api/v1", tags=["v1:notifications"])


@router.get("/notifications", response_model=NotificationStatus)
def get_notifications(rt: RuntimeDep) -> dict[str, object]:
    return rt.notifier.status()


@router.post("/notifications/test", response_model=dict)
def test_notification(rt: RuntimeDep) -> dict[str, object]:
    """同步发一条测试消息，配置完 webhook 可以立刻验证。"""
    return rt.notifier.test()
