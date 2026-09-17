"""v1：定时任务（保存 / 备份 / 定时重启）。

配置在 docker-compose.yml 的 environment 里（SCHEDULE_*），这里只负责查看与手动触发。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RuntimeDep
from app.schemas.v1 import SchedulerResponse
from app.core.errors import NotFound

router = APIRouter(prefix="/api/v1", tags=["v1:scheduler"])


@router.get("/scheduler", response_model=SchedulerResponse)
def get_scheduler(rt: RuntimeDep) -> dict[str, object]:
    return {
        "enabled": rt.settings.schedule_enabled,
        "timezone": rt.scheduler.tz.key,
        "jobs": rt.scheduler.list_jobs(),
    }


@router.post("/scheduler/{name}/run", response_model=dict)
def run_job(name: str, rt: RuntimeDep) -> dict[str, object]:
    """手动触发一次（同步执行，备份/保存都很快；restart 会返回 submitted）。"""
    try:
        return rt.scheduler.run_now(name)
    except KeyError:
        known = sorted(rt.scheduler.specs)
        raise NotFound(f"没有名为 {name} 的定时任务", details={"jobs": known}) from None
