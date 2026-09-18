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
from app.services.operations import EXCLUSIVE_KINDS, operation_kinds

CHANGELOG_URL = (
    "https://github.com/tyrantblue/terraria-server/blob/main/docs/api/CHANGELOG.md"
)

#: 当前后端具备的能力，前端可据此决定是否显示某些 UI。
#: 新增能力时同时在这里、`CAPABILITY_SINCE` 与 docs/api/v1.md 的 §1 登记；
#: 测试会锁住「两边一致」，避免漏登记。
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
    "guard.state",
    "scheduler.jobs",
    "backups.list",
    "notifications.status",
    "meta.handshake",
]

#: 能力 → 引入版本（issue #14）。面板遇到老后端时，用 `since` 把提示写成
#: 「需要 API ≥ x.y」，而不是笼统的「不支持」。**能力只增不减**：新增能力
#: 属于 minor（与 SemVer 约定一致，见 docs/api/CHANGELOG.md）。
CAPABILITY_SINCE: dict[str, str] = {
    "server.status": "1.2.0",
    "server.console": "1.2.0",
    "players.list": "1.2.0",
    "world.list": "1.2.0",
    "world.upload": "1.2.0",
    "world.switch": "1.2.0",
    "meta.handshake": "1.2.0",
    "world.metadata": "2.0.0",
    "config.password_masked": "2.0.0",
    "console.audit.persistent": "2.0.0",
    "server.log_health": "2.0.0",
    "server.metrics": "2.0.0",
    # 这四个页面（守卫/定时任务/备份/通知）功能更早就有了，但作为 capability
    # 是从 2.1.0 才开始 advertise 的——老后端没有这一项。
    "guard.state": "2.1.0",
    "scheduler.jobs": "2.1.0",
    "backups.list": "2.1.0",
    "notifications.status": "2.1.0",
}

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
        "capabilities": list(CAPABILITIES),
        "capability_since": dict(CAPABILITY_SINCE),
        "operation_kinds": operation_kinds(),
        "exclusive_operation_kinds": sorted(EXCLUSIVE_KINDS),
        "deprecations": [],
        "links": MetaLinks(
            openapi="/openapi.json",
            docs="/docs",
            changelog=CHANGELOG_URL,
        ),
    }
