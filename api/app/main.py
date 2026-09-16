from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.server import (
    router as server_router,
)
from app.routers.world import (
    router as world_router,
)
from app.routers.console import (
    router as console_router,
)


app = FastAPI(
    title="Terraria Server API",
    version="1.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(
    server_router,
)

app.include_router(
    world_router,
)

app.include_router(
    console_router,
)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "terraria-api",
    }
