"""v1：世界存档、备份与切换。

切换世界在旧版里是一个同步请求（sleep + 最多 30 秒轮询），现在是一个后台操作：
`POST /api/v1/worlds/{file}/activate` 立刻返回 202 + operation_id。
"""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile, status

from app.api.deps import RuntimeDep
from app.schemas.v1 import (
    BackupListResponse,
    OkResponse,
    OperationRef,
    UploadResponse,
    WorldListResponse,
)

router = APIRouter(prefix="/api/v1", tags=["v1:worlds"])


@router.get("/worlds", response_model=WorldListResponse)
def list_worlds(rt: RuntimeDep) -> dict[str, object]:
    worlds, active = rt.world.list()
    return {
        "worlds": worlds,
        "active_world": active,
        "backup_dir": str(rt.world.backup_dir),
    }


@router.post("/worlds", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_world(rt: RuntimeDep, file: UploadFile = File(...)) -> dict[str, object]:
    name, filename, size = await rt.world.upload(file.filename, file)
    return {"name": name, "file": filename, "size": size}


@router.delete("/worlds/{file}", response_model=OkResponse)
def delete_world(file: str, rt: RuntimeDep) -> dict[str, object]:
    rt.world.delete(file)
    return {"ok": True}


@router.post(
    "/worlds/{file}/activate",
    response_model=OperationRef,
    status_code=status.HTTP_202_ACCEPTED,
)
def activate_world(file: str, rt: RuntimeDep) -> dict[str, object]:
    operation = rt.world.activate(file)
    return {"operation_id": operation.id, "state": operation.state, "kind": operation.kind}


@router.post(
    "/worlds/{file}/backup",
    response_model=OperationRef,
    status_code=status.HTTP_202_ACCEPTED,
)
def backup_world(file: str, rt: RuntimeDep) -> dict[str, object]:
    operation = rt.world.backup(file)
    return {"operation_id": operation.id, "state": operation.state, "kind": operation.kind}


@router.get("/backups", response_model=BackupListResponse)
def list_backups(rt: RuntimeDep) -> dict[str, object]:
    return {"backups": rt.world.list_backups()}
