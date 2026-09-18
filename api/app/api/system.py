"""系统级接口：健康检查与版本握手。

`GET /api/meta` 是给前端的「保险丝」：面板启动时先问一次，如果 api_version
或 min_client_version 与面板构建时预期的不一致，就直接提示用户刷新/更新面板，
而不是让用户面对一堆 404 或缺失字段。

2.0.0（2026-09-19）：旧 `/api/*` 资源路由已删除，因此
* `GET /api/meta/usage`（弃用调用量统计）一并移除；
* `deprecations` 字段保留但恒为 `[]`，前端可以不再依赖它。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RuntimeDep
from app.schemas.common import HealthResponse, MetaLinks, MetaResponse

CHANGELOG_URL = (
    "https://github.com/tyrantblue/terraria-server/blob/main/docs/api/CHANGELOG.md"
)

#: 当前后端具备的能力，前端可据此决定是否显示某些 UI。
#: 新增能力时同时在这里和 docs/api/v1.md 的 §1 登记。
CAPABILITIES = [
    "server.status",
    "server.console",
    "players.list",
    "world.list",
    "world.upload",
    "world.switch",
    "world.metadata",
    "config.password_masked",
    "console.audit.persistent",
    "server.log_health",
    "server.metrics",
    "meta.handshake",
]

router = APIRouter(tags=["system"])


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
        "deprecations": [],
        "links": MetaLinks(
            openapi="/openapi.json",
            docs="/docs",
            changelog=CHANGELOG_URL,
        ),
    }
