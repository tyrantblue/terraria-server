"""聚合 /api/v1 下的所有路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    console,
    notifications,
    operations,
    players,
    scheduler,
    server,
    worlds,
)

api_v1 = APIRouter()
api_v1.include_router(server.router)
api_v1.include_router(players.router)
api_v1.include_router(console.router)
api_v1.include_router(worlds.router)
api_v1.include_router(operations.router)
api_v1.include_router(scheduler.router)
api_v1.include_router(notifications.router)

__all__ = ["api_v1"]
