"""v1：资源与在线人数曲线（issue #2 第 2 部分）。

采样在后端做（见 services/metrics.py），这里只负责把内存里的点读出来。
`?minutes=N` 决定回看窗口，默认最近 1 小时、最长 24 小时（= 默认保留点数）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import RuntimeDep
from app.schemas.v1 import MetricsResponse

router = APIRouter(prefix="/api/v1", tags=["v1:metrics"])

#: 与 settings.metrics_retention_points 的默认值一致（1 分钟粒度 × 24 小时）
MAX_WINDOW_MINUTES = 1440.0


@router.get("/metrics", response_model=MetricsResponse)
def metrics(
    rt: RuntimeDep,
    minutes: float = Query(default=60.0, gt=0, le=MAX_WINDOW_MINUTES),
) -> dict[str, object]:
    """返回窗口内的采样点（时间升序）+ 最新一点。

    读不到的字段是 `null`（例如非 Linux/cgroup 环境拿不到内存用量），
    前端画曲线时跳过空点即可。API 重启后历史清空——这只是内存里的近期曲线。
    """
    latest = rt.metrics.latest()
    return {
        "interval_seconds": rt.metrics.interval,
        "retention_points": rt.metrics.retention,
        "window_minutes": minutes,
        "latest": latest.as_dict() if latest else None,
        "points": [point.as_dict() for point in rt.metrics.snapshot(minutes=minutes)],
    }
