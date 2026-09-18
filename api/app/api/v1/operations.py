"""v1：长耗时操作的查询。

配合 `202 Accepted` 使用：世界切换 / 服务端重启 / 配置生效都会返回 operation_id，
前端轮询 `GET /api/v1/operations/{id}` 看进度，不再把一个 HTTP 请求挂 30 秒。

保留策略（issue #15）：内存里最多 50 条、新的在前，API 重启即清空。查不到
operation 时用专用错误码 `operation_not_found`（区别于路由不存在的 `not_found`）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import RuntimeDep
from app.schemas.v1 import OperationList, OperationView

router = APIRouter(prefix="/api/v1", tags=["v1:operations"])


@router.get("/operations", response_model=OperationList)
def list_operations(
    rt: RuntimeDep,
    state: str | None = Query(
        default=None,
        description="只返回该状态：pending | running | succeeded | failed",
    ),
) -> dict[str, object]:
    operations = rt.operations.list(state=state)
    return {
        "operations": [operation.as_dict() for operation in operations],
        "in_flight": rt.operations.in_flight(),
    }


@router.get("/operations/{operation_id}", response_model=OperationView)
def get_operation(operation_id: str, rt: RuntimeDep) -> dict[str, object]:
    return rt.operations.get(operation_id).as_dict()
