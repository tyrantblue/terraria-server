"""server 相关出入参模型。

字段顺序、字段名与旧实现完全一致（旧响应是裸 dict，字段顺序即 JSON 顺序）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import SuccessResponse


# ---------------------------------------------------------------- 请求
class CommandRequest(BaseModel):
    command: str


class PlayerRequest(BaseModel):
    player: str


class MessageRequest(BaseModel):
    message: str


class MotdRequest(BaseModel):
    motd: str


class PasswordRequest(BaseModel):
    password: str


class MaxPlayersRequest(BaseModel):
    max_players: int = Field(ge=1, le=255)


# ---------------------------------------------------------------- 响应
class PlayerList(BaseModel):
    online: int
    list: list[str]


class ServerStatusResponse(BaseModel):
    running: bool
    version: str | None
    port: int | None
    max_players: int | None
    time: str | None
    seed: str | None
    motd: str | None
    players: PlayerList


class PlayersResponse(BaseModel):
    online: int
    players: list[str]


class ConsoleResponse(BaseModel):
    lines: list[str]


class MaxPlayersResponse(SuccessResponse):
    max_players: int


class MessageResponse(SuccessResponse):
    message: str


class PlayerResponse(SuccessResponse):
    player: str


class MotdResponse(SuccessResponse):
    motd: str
