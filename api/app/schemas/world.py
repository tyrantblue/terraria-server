"""world 相关出入参模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import SuccessResponse


class SwitchWorldRequest(BaseModel):
    file: str


class WorldItem(BaseModel):
    name: str
    file: str
    size: int
    modified_at: float
    active: bool


class WorldListResponse(BaseModel):
    worlds: list[WorldItem]
    active_world: str | None


class WorldUploadResponse(SuccessResponse):
    name: str
    file: str
    size: int


class WorldSwitchResponse(SuccessResponse):
    world: str
    message: str
