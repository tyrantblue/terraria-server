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
    operation_ref,
)
from app.services import config_service
from app.services.status import ServerSnapshot

router = APIRouter(prefix="/api/v1", tags=["v1:server"])


def _server_state(rt: RuntimeDep) -> dict[str, object]:
    # 控制台不可用且没有缓存时，StatusCollector.get() 会抛异常。以前这会变成
    # 500，面板显示 "API Unreachable"——其实 API 好得很，是游戏服停了
    # （issue #11）。现在兜成 200 + running=false + 读不到的字段为 null。
    try:
        snapshot = rt.server.status()
    except Exception:  # noqa: BLE001 - 任何探测失败都降级成「未运行」
        snapshot = ServerSnapshot(running=False)
    running = snapshot.running

    worlds, active = rt.world.list()
    world = next((item for item in worlds if item["file"] == active), None)
    config = rt.config.load()
    log_stalled, log_age = rt.server.log_health(running=running)
    return {
        "running": running,
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
        # 敏感键（password）只回掩码，不回明文——见 issue #6
        "config": config_service.mask_secrets(
            {key: config[key] for key in sorted(config)}
        ),
        "password_set": config_service.secret_is_set(config, "password"),
        # 日志管道健康：能读到 version 才算「服务端在运行」
        "log_stalled": log_stalled,
        "log_age": log_age,
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
    return operation_ref(rt.server.restart())


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
        "values": config_service.mask_secrets(printable),
        "password_set": config_service.secret_is_set(values, "password"),
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
    """写配置。

    `password` 的语义（掩码之后必须写死，见 issue #6）：

    * **不带 `password` 键** = 不修改（前端输入框留空时就不要发送这个键）；
    * **带非空值** = 设为该密码；
    * **带空字符串 `""`** = **清空密码**（面板的「留空即移除」用的就是这条）；
    * **带掩码 `"••••••"`** = 400：这是 GET 的回显值，不能被当成新密码写回去。
    """
    result = rt.server.update_config(
        request.values,
        apply=request.apply,
        confirm_low_max_players=request.confirm_low_max_players,
    )
    if result.get("operation_id"):
        response.status_code = status.HTTP_202_ACCEPTED
    return result
