"""world 路由（URL 与响应体与旧实现保持一致）。"""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from app.api.deps import RuntimeDep
from app.schemas.world import (
    SwitchWorldRequest,
    WorldListResponse,
    WorldSwitchResponse,
    WorldUploadResponse,
)

router = APIRouter(prefix="/api/world", tags=["world"])


@router.get("/list", response_model=WorldListResponse)
def list_worlds(rt: RuntimeDep) -> dict[str, object]:
    worlds, active = rt.world.list()
    return {"worlds": worlds, "active_world": active}


@router.post("/upload", response_model=WorldUploadResponse)
async def upload_world(rt: RuntimeDep, file: UploadFile = File(...)) -> dict[str, object]:
    name, filename, size = await rt.world.upload(file.filename, file)
    return {"success": True, "name": name, "file": filename, "size": size}


@router.post("/switch", response_model=WorldSwitchResponse)
def switch_world(request: SwitchWorldRequest, rt: RuntimeDep) -> dict[str, object]:
    filename = rt.world.switch(request.file)
    return {
        "success": True,
        "world": filename,
        "message": "world switched successfully",
    }
