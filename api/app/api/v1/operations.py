"""v1：长耗时操作的查询。

配合 `202 Accepted` 使用：世界切换 / 服务端重启 / 配置生效都会返回 operation_id，
前端轮询 `GET /api/v1/operations/{id}` 看进度，不再把一个 HTTP 请求挂 30 秒。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RuntimeDep
from app.schemas.v1 import OperationList, OperationView

router = APIRouter(prefix="/api/v1", tags=["v1:operations"])


@router.get("/operations", response_model=OperationList)
def list_operations(rt: RuntimeDep) -> dict[str, object]:
    return {"operations": [operation.as_dict() for operation in rt.operations.list()]}


@router.get("/operations/{operation_id}", response_model=OperationView)
def get_operation(operation_id: str, rt: RuntimeDep) -> dict[str, object]:
    return rt.operations.get(operation_id).as_dict()
