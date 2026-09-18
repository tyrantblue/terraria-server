"""FastAPI 应用装配。

P0 重构后这里只负责：中间件、统一异常处理、路由注册。
业务逻辑在 app/services/，数据形状在 app/schemas/。

2.0.0 起旧 `/api/*` 资源路由已删除（见 docs/api/CHANGELOG.md），
这里只注册 `/api/v1` 与系统级接口（health / meta）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import system as system_api
from app.api.v1 import api_v1
from app.core.errors import AppError
from app.core.settings import API_VERSION
from app.services.runtime import Runtime, get_runtime
from app.services.world_service import precheck_upload

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
    413: "payload_too_large",
    422: "validation_failed",
    429: "too_many_requests",
    500: "internal_error",
    502: "upstream_failed",
    503: "service_unavailable",
    504: "gateway_timeout",
    507: "insufficient_storage",
}

#: 上传世界的路径（中间件据此做 Content-Length 预检）
UPLOAD_PATH = "/api/v1/worlds"


def create_app(runtime: Runtime | None = None) -> FastAPI:
    """构造应用。

    传入 runtime 时（测试/嵌入使用），请求依赖与生命周期都用它，
    避免「依赖注入用假环境、启动钩子却碰真目录」这种坑。
    """

    def resolve_runtime() -> Runtime:
        return runtime if runtime is not None else get_runtime()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        rt = resolve_runtime()
        rt.notifier.start()
        rt.events.start()
        if rt.settings.metrics_interval_seconds > 0:
            rt.metrics.start()
        if rt.settings.schedule_enabled:
            rt.scheduler.start()
        try:
            yield
        finally:
            rt.scheduler.stop()
            rt.metrics.stop()
            rt.events.stop()
            rt.notifier.stop()

    app = FastAPI(title="Terraria Server API", version=API_VERSION, lifespan=lifespan)
    app.state.runtime = runtime

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
    async def upload_precheck(request: Request, call_next):
        """上传世界前先用 Content-Length 拦一次：413（超限）/ 507（磁盘不足）。

        必须在路由之前：Starlette 解析 multipart 时会把整个请求体落到临时文件，
        等路由函数拿到 `UploadFile` 时磁盘已经被写过了。service 层还有同样的检查，
        用于没有 Content-Length 的 chunked 上传。
        """
        if request.method == "POST" and request.url.path == UPLOAD_PATH:
            try:
                declared = int(request.headers.get("content-length") or 0)
            except ValueError:
                declared = 0
            if declared > 0:
                settings = resolve_runtime().settings
                try:
                    precheck_upload(
                        settings.worlds_dir,
                        declared_size=declared,
                        max_bytes=settings.world_upload_max_bytes,
                    )
                except AppError as exc:
                    return JSONResponse(status_code=exc.status_code, content=exc.to_body())
        return await call_next(request)

    @app.middleware("http")
    async def client_version_header(request: Request, call_next):
        """回显 `X-Client-Version`，方便面板确认自己连的是哪个后端。

        这个头以前还用于统计被弃用接口的调用量；2.0.0 删掉旧接口后只做回显。
        """
        response = await call_next(request)
        client_version = request.headers.get("X-Client-Version")
        if client_version:
            response.headers["X-Client-Version"] = client_version
        return response

    if runtime is not None:
        from app.api.deps import runtime as runtime_dep

        app.dependency_overrides[runtime_dep] = resolve_runtime

    app.include_router(system_api.router)
    app.include_router(api_v1)
    return app


app = create_app()
