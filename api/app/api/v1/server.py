"""v1：服务器状态与配置。

* `GET  /api/v1/server`  —— 一次拿全（版本/端口/上限/时间/种子/MOTD/玩家/世界/配置），
  取代旧版「7 个查询型 POST + /status + /world/list」；
* `POST /api/v1/server/actions`  —— save / settle；
* `POST /api/v1/server/restart`  —— 202 + 操作 id（保存 → 关闭 → 等待恢复）；
* `POST /api/v1/server/time`     —— 取代 4 个 /time/{phase}；
* `GET/PUT /api/v1/config`       —— 明确「写文件（持久化）」与「立即生效」的区别。
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.api.deps import RuntimeDep
from app.schemas.v1 import (
    ActionRequest,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
    ConfigView,
    OkResponse,
    OperationRef,
    ServerState,
    TimeRequest,
    TimeResponse,
)
from app.services import config_service

router = APIRouter(prefix="/api/v1", tags=["v1:server"])


def _server_state(rt: RuntimeDep) -> dict[str, object]:
    snapshot = rt.server.status()
    worlds, active = rt.world.list()
    world = next((item for item in worlds if item["file"] == active), None)
    config = rt.config.load()
    return {
        "running": snapshot.running,
        "version": snapshot.version,
        "port": snapshot.port,
        "max_players": snapshot.max_players,
        "time": snapshot.game_time,
        "seed": snapshot.seed,
        "motd": snapshot.motd,
        "players": {
            "online": len(snapshot.player_entries),
            "max": snapshot.max_players,
            "players": [
                {"name": entry.name, "ip": entry.ip, "port": entry.port}
                for entry in snapshot.player_entries
            ],
        },
        "world": world,
        "config": {key: config[key] for key in sorted(config)},
    }


@router.get("/server", response_model=ServerState)
def get_server(rt: RuntimeDep) -> dict[str, object]:
    return _server_state(rt)


@router.post("/server/actions", response_model=OkResponse)
def server_action(request: ActionRequest, rt: RuntimeDep) -> dict[str, object]:
    if request.action == "save":
        rt.server.save()
    else:
        rt.server.settle()
    return {"ok": True}


@router.post(
    "/server/restart",
    response_model=OperationRef,
    status_code=status.HTTP_202_ACCEPTED,
)
def restart_server(rt: RuntimeDep) -> dict[str, object]:
    operation = rt.server.restart()
    return {
        "operation_id": operation.id,
        "state": operation.state,
        "kind": operation.kind,
    }


@router.post("/server/time", response_model=TimeResponse)
def set_time(request: TimeRequest, rt: RuntimeDep) -> dict[str, object]:
    phase = rt.server.set_time_phase(request.phase)
    return {"phase": phase, "time": rt.server.status().game_time}


# ---------------------------------------------------------------- config
@router.get("/config", response_model=ConfigView)
def get_config(rt: RuntimeDep) -> dict[str, object]:
    values = rt.config.load()
    printable = {k: v for k, v in values.items() if k in config_service.EDITABLE_KEYS}
    if "world" in printable:
        printable["world"] = config_service.world_filename(printable["world"]) or printable["world"]
    return {
        "values": printable,
        "editable_keys": sorted(config_service.EDITABLE_KEYS),
        "runtime_keys": sorted(config_service.RUNTIME_KEYS),
        "restart_keys": sorted(config_service.RESTART_KEYS),
        "path": str(rt.config.path),
    }


@router.put("/config", response_model=ConfigUpdateResponse)
def update_config(
    request: ConfigUpdateRequest,
    response: Response,
    rt: RuntimeDep,
) -> dict[str, object]:
    result = rt.server.update_config(
        request.values,
        apply=request.apply,
        confirm_low_max_players=request.confirm_low_max_players,
    )
    if result.get("operation_id"):
        response.status_code = status.HTTP_202_ACCEPTED
    return result
