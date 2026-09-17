"""FastAPI 应用装配。

P0 重构后这里只负责：中间件、统一异常处理、路由注册。
业务逻辑在 app/services/，数据形状在 app/schemas/。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import console as console_api
from app.api import server as server_api
from app.api import system as system_api
from app.api import world as world_api
from app.api.v1 import api_v1
from app.core.deprecations import lookup as lookup_deprecation
from app.core.errors import AppError
from app.core.settings import API_VERSION
from app.core.telemetry import telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

#: 旧接口与新接口共用的错误响应形状（见 core/errors.py）
_HTTP_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_failed",
    429: "too_many_requests",
    500: "internal_error",
    502: "upstream_failed",
    503: "service_unavailable",
    504: "gateway_timeout",
}


def create_app() -> FastAPI:
    app = FastAPI(title="Terraria Server API", version=API_VERSION)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_body())

    @app.exception_handler(HTTPException)
    async def handle_http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        body = {
            "detail": exc.detail,  # 保留旧字段，前端只读 detail 也不会坏
            "error": {
                "code": _HTTP_ERROR_CODES.get(exc.status_code, "error"),
                "message": message,
            },
        }
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.middleware("http")
    async def deprecation_headers(request: Request, call_next):
        """给被弃用的旧路由加 Deprecation/Sunset/Link 头，并记录调用量。

        纯附加行为：不改变状态码、不改变响应体，旧前端完全无感。
        """
        response = await call_next(request)
        deprecation = lookup_deprecation(request.url.path)
        if deprecation is not None:
            for key, value in deprecation.headers().items():
                response.headers[key] = value
            telemetry.record(request.url.path, request.headers.get("X-Client-Version"))
        return response

    app.include_router(system_api.router)
    app.include_router(server_api.router)
    app.include_router(world_api.router)
    app.include_router(console_api.router)
    app.include_router(api_v1)
    return app


app = create_app()
