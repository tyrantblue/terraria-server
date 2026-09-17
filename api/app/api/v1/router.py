"""聚合 /api/v1 下的所有路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import console, operations, players, server, worlds

api_v1 = APIRouter()
api_v1.include_router(server.router)
api_v1.include_router(players.router)
api_v1.include_router(console.router)
api_v1.include_router(worlds.router)
api_v1.include_router(operations.router)

__all__ = ["api_v1"]
