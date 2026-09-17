"""系统级接口：健康检查与版本握手。

`GET /api/meta` 是给前端的「保险丝」：面板启动时先问一次，如果 api_version
或 min_client_version 与面板构建时预期的不一致，就直接提示用户刷新/更新面板，
而不是让用户面对一堆 404 或缺失字段。

新增接口，不影响任何旧接口。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RuntimeDep
from app.core.deprecations import all_deprecations
from app.core.telemetry import telemetry
from app.schemas.common import HealthResponse, MetaLinks, MetaResponse
from app.schemas.v1 import UsageResponse

CHANGELOG_URL = (
    "https://github.com/tyrantblue/terraria-server/blob/main/docs/api/CHANGELOG.md"
)

#: 当前后端具备的能力，前端可据此决定是否显示某些 UI
CAPABILITIES = [
    "server.status",
    "server.console",
    "players.list",
    "world.list",
    "world.upload",
    "world.switch",
    "meta.handshake",
]

router = APIRouter(tags=["system"])


@router.get("/api/meta/usage", response_model=UsageResponse)
def meta_usage() -> dict[str, object]:
    """被弃用接口的调用量：用数据判断「前端已经迁完」再删旧路由。"""
    return {
        "note": (
            "计数在 API 进程内存里，重启即清零；client_versions 来自请求头 "
            "X-Client-Version（面板应带上自己的构建版本）"
        ),
        "usage": telemetry.snapshot(),
    }


@router.get("/api/health", response_model=HealthResponse)
def health() -> dict[str, str]:
    return {"status": "ok", "service": "terraria-api"}


@router.get("/api/meta", response_model=MetaResponse)
def meta(rt: RuntimeDep) -> dict[str, object]:
    server_version: str | None = None
    try:
        server_version = rt.server.status().version
    except Exception:  # noqa: BLE001 - 服务端在重启时也要能握手
        server_version = None

    return {
        "api_version": rt.settings.api_version,
        "min_client_version": rt.settings.min_client_version,
        "server_version": server_version,
        "capabilities": CAPABILITIES,
        "deprecations": all_deprecations(),
        "links": MetaLinks(
            openapi="/openapi.json",
            docs="/docs",
            changelog=CHANGELOG_URL,
        ),
    }
