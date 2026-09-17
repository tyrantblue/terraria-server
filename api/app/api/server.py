"""server 路由（URL 与响应体与旧实现保持一致）。

这一层只做 HTTP：路径、状态码、依赖注入、业务调用、响应拼装。
命令拼接、日志解析、配置读写全部在 app/services/ 里。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RuntimeDep
from app.schemas.common import CommandResponse, SuccessResponse
from app.services.console.parser import is_fence_line
from app.schemas.server import (
    CommandRequest,
    ConsoleResponse,
    MaxPlayersRequest,
    MaxPlayersResponse,
    MessageRequest,
    MessageResponse,
    MotdRequest,
    MotdResponse,
    PasswordRequest,
    PlayerRequest,
    PlayerResponse,
    PlayersResponse,
    ServerStatusResponse,
)

router = APIRouter(prefix="/api/server", tags=["server"])


# ---------------------------------------------------------------- 查询
@router.get("/status", response_model=ServerStatusResponse)
def status(rt: RuntimeDep) -> dict[str, object]:
    snapshot = rt.server.status()
    return {
        "running": snapshot.running,
        "version": snapshot.version,
        "port": snapshot.port,
        "max_players": snapshot.max_players,
        "time": snapshot.game_time,
        "seed": snapshot.seed,
        "motd": snapshot.motd,
        "players": {
            "online": len(snapshot.players),
            "list": snapshot.players,
        },
    }


@router.get("/players", response_model=PlayersResponse)
def players(rt: RuntimeDep) -> dict[str, object]:
    online = rt.server.players()
    return {"online": len(online), "players": online}


@router.get("/console", response_model=ConsoleResponse)
def console(rt: RuntimeDep) -> dict[str, object]:
    lines = rt.reader.tail(rt.settings.console_tail_default)
    # 哨兵回显是请求/响应协议的内部开销，不暴露给面板
    return {"lines": [line for line in lines if not is_fence_line(line)]}


# ---------------------------------------------------------------- 通用命令
@router.post("/command", response_model=CommandResponse)
def command(request: CommandRequest, rt: RuntimeDep) -> dict[str, object]:
    rt.server.run_command(request.command)
    # 与旧实现一致：回显的是原始请求里的字符串
    return {"success": True, "command": request.command}


# ---------------------------------------------------------------- 生命周期
@router.post("/save", response_model=CommandResponse)
def save(rt: RuntimeDep) -> dict[str, object]:
    rt.server.save()
    return {"success": True, "command": "save"}


@router.post("/settle", response_model=CommandResponse)
def settle(rt: RuntimeDep) -> dict[str, object]:
    rt.server.settle()
    return {"success": True, "command": "settle"}


@router.post("/playing", response_model=CommandResponse)
def playing(rt: RuntimeDep) -> dict[str, object]:
    rt.server.players()
    return {"success": True, "command": "playing"}


@router.post("/version", response_model=CommandResponse)
def version(rt: RuntimeDep) -> dict[str, object]:
    rt.server.status()
    return {"success": True, "command": "version"}


@router.post("/port", response_model=CommandResponse)
def port(rt: RuntimeDep) -> dict[str, object]:
    rt.server.status()
    return {"success": True, "command": "port"}


# ---------------------------------------------------------------- 运行时设置
@router.post("/maxplayers", response_model=MaxPlayersResponse)
def maxplayers(request: MaxPlayersRequest, rt: RuntimeDep) -> dict[str, object]:
    value = rt.server.set_max_players(request.max_players)
    return {"success": True, "max_players": value}


@router.post("/motd", response_model=MotdResponse)
def motd(request: MotdRequest, rt: RuntimeDep) -> dict[str, object]:
    value = rt.server.set_motd(request.motd)
    return {"success": True, "motd": value}


@router.post("/password", response_model=SuccessResponse)
def password(request: PasswordRequest, rt: RuntimeDep) -> dict[str, object]:
    rt.server.set_password(request.password)
    return {"success": True}


# ---------------------------------------------------------------- 玩家/广播
@router.post("/say", response_model=MessageResponse)
def say(request: MessageRequest, rt: RuntimeDep) -> dict[str, object]:
    message = rt.server.say(request.message)
    return {"success": True, "message": message}


@router.post("/kick", response_model=PlayerResponse)
def kick(request: PlayerRequest, rt: RuntimeDep) -> dict[str, object]:
    player = rt.server.kick(request.player)
    return {"success": True, "player": player}


@router.post("/ban", response_model=PlayerResponse)
def ban(request: PlayerRequest, rt: RuntimeDep) -> dict[str, object]:
    player = rt.server.ban(request.player)
    return {"success": True, "player": player}


# ---------------------------------------------------------------- 时间
@router.post("/time/dawn", response_model=CommandResponse)
def dawn(rt: RuntimeDep) -> dict[str, object]:
    rt.server.set_time_phase("dawn")
    return {"success": True, "command": "dawn"}


@router.post("/time/noon", response_model=CommandResponse)
def noon(rt: RuntimeDep) -> dict[str, object]:
    rt.server.set_time_phase("noon")
    return {"success": True, "command": "noon"}


@router.post("/time/dusk", response_model=CommandResponse)
def dusk(rt: RuntimeDep) -> dict[str, object]:
    rt.server.set_time_phase("dusk")
    return {"success": True, "command": "dusk"}


@router.post("/time/midnight", response_model=CommandResponse)
def midnight(rt: RuntimeDep) -> dict[str, object]:
    rt.server.set_time_phase("midnight")
    return {"success": True, "command": "midnight"}
