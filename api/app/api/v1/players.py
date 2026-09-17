"""v1：玩家与广播。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RuntimeDep
from app.core.errors import NotFound
from app.schemas.v1 import (
    BanListResponse,
    BroadcastRequest,
    OkResponse,
    PlayerCollection,
)
from app.services.console.parser import PlayerEntry

router = APIRouter(prefix="/api/v1", tags=["v1:players"])

BANLIST_NOTE = (
    "原版控制台只有 ban、没有 unban，且只会给在线玩家写名单；"
    "banlist.txt 在第一次封禁之前不存在。这里的 DELETE 只负责按行从文件里移除。"
)


def _require_online(rt: RuntimeDep, name: str) -> PlayerEntry:
    for entry in rt.status.player_entries():
        if entry.name == name:
            return entry
    raise NotFound(f"player not online: {name}")


@router.get("/players", response_model=PlayerCollection)
def list_players(rt: RuntimeDep) -> dict[str, object]:
    snapshot = rt.server.status()
    return {
        "online": len(snapshot.player_entries),
        "max": snapshot.max_players,
        "players": [
            {"name": entry.name, "ip": entry.ip, "port": entry.port}
            for entry in snapshot.player_entries
        ],
    }


@router.post("/players/{name}/kick", response_model=OkResponse)
def kick_player(name: str, rt: RuntimeDep) -> dict[str, object]:
    _require_online(rt, name)
    rt.server.kick(name)
    return {"ok": True}


@router.post("/players/{name}/ban", response_model=OkResponse)
def ban_player(name: str, rt: RuntimeDep) -> dict[str, object]:
    _require_online(rt, name)
    rt.server.ban(name)
    return {"ok": True}


@router.delete("/players/{name}/ban", response_model=OkResponse)
def unban_player(name: str, rt: RuntimeDep) -> dict[str, object]:
    if not rt.banlist.remove(name):
        raise NotFound(f"{name} 不在封禁名单里（或 banlist.txt 还不存在）")
    return {"ok": True}


@router.get("/bans", response_model=BanListResponse)
def list_bans(rt: RuntimeDep) -> dict[str, object]:
    return {
        "bans": rt.banlist.entries(),
        "source": str(rt.banlist.path),
        "exists": rt.banlist.exists(),
        "note": None if rt.banlist.exists() else BANLIST_NOTE,
    }


@router.post("/broadcast", response_model=OkResponse)
def broadcast(request: BroadcastRequest, rt: RuntimeDep) -> dict[str, object]:
    rt.server.say(request.message)
    return {"ok": True}
