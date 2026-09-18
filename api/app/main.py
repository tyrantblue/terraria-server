"""FastAPI 应用装配。

P0 重构后这里只负责：中间件、统一异常处理、路由注册。
业务逻辑在 app/services/，数据形状在 app/schemas/。

2.0.0 起旧 `/api/*` 资源路由已删除（见 docs/api/CHANGELOG.md），
这里只注册 `/api/v1` 与系统级接口（health / meta）。

2.1.0 起补上了几项加固（issue #13 / #16 / #17）：

* 中间件顺序调整为「客户端版本回显 → CORS → 客户端版本门槛 → 写鉴权 →
  限流 → 上传预检」，保证**任何早退响应**（413/429/401/507）都带 CORS 头，
  浏览器能读到可读原因而不是「连接中断」；
* pydantic 校验失败（422）也走统一错误信封；
* 写操作支持限流与可选的共享 token；
* `X-Client-Version` 低于 `min_client_version` 时明确拒绝。
"""

from __future__ import annotations

import hmac
import logging
import math
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import system as system_api
from app.api.v1 import api_v1
from app.core.errors import AppError
from app.core.settings import API_VERSION, Settings
from app.core.version import looks_like_version, version_lt
from app.services.ratelimit import WINDOW_SECONDS, RateLimiter
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

#: 会改状态的方法。限流与写鉴权都只看这些。
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: 这些路径永远放行客户端版本门槛：老面板要靠它们才知道自己该升级。
_VERSION_EXEMPT_PATHS = frozenset({"/api/meta", "/api/health"})


def _error_body(code: str, message: str, details: dict | None = None) -> dict:
    error: dict = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"detail": message, "error": error}


def _bearer_token(header: str | None) -> str:
    if not header:
        return ""
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


def _rate_bucket(path: str, settings: Settings) -> tuple[str, int]:
    if path == "/api/v1/console/commands":
        return "console", settings.rate_limit_console_per_minute
    if path == "/api/v1/server/restart":
        return "restart", settings.rate_limit_restart_per_minute
    return "write", settings.rate_limit_write_per_minute


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

    #: 限流器是**进程内**的：单进程管理面板够用，多副本部署要改成共享存储。
    limiter = RateLimiter()

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_body())

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # 注册在 Starlette 的 HTTPException 上（FastAPI 的那个是它的子类），
        # 这样「路由不存在 / 方法不允许」等框架级错误也走统一信封并带
        # error.code（issue #15：路由 404 = not_found，operation 404 =
        # operation_not_found，两者可区分）。
        message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        body = {
            "detail": exc.detail,  # 保留旧字段，前端只读 detail 也不会坏
            "error": {
                "code": _HTTP_ERROR_CODES.get(exc.status_code, "error"),
                "message": message,
            },
        }
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """pydantic 校验失败也走统一信封（issue #16.3）。

        `detail` 仍保留原来的错误数组以兼容旧客户端（前端有
        `formatValidationErrors()` 专门读它）；新增稳定的 `error.code`。
        """
        errors = jsonable_encoder(exc.errors())
        return JSONResponse(
            status_code=422,
            content={
                "detail": errors,
                "error": {
                    "code": "validation_failed",
                    "message": "request validation failed",
                    "details": {"errors": errors},
                },
            },
        )

    # ------------------------------------------------------------------
    # 中间件顺序很关键。Starlette 的 `add_middleware` 是 `insert(0)`，
    # 而 `@app.middleware("http")` 内部就是 `add_middleware`，所以**后定义的在
    # 外层**。这里的定义顺序让最终链条从外到内为：
    #     client_version_header → CORSMiddleware → client_version_gate
    #       → write_auth → rate_limit → upload_precheck → 路由
    # 于是任何早退响应都会经过 CORSMiddleware 与客户端版本回显（issue #13）。
    # ------------------------------------------------------------------

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
    async def rate_limit(request: Request, call_next):
        """写操作的滑动窗口限流（issue #17.2）。429 从此真正可达。"""
        if request.method in _UNSAFE_METHODS:
            settings = resolve_runtime().settings
            if settings.rate_limit_enabled:
                bucket, limit = _rate_bucket(request.url.path, settings)
                key = request.client.host if request.client else "unknown"
                retry_after = limiter.check(bucket, key, limit)
                if retry_after > 0:
                    message = (
                        f"请求过于频繁：{bucket} 每 {int(WINDOW_SECONDS)} 秒最多 {limit} 次"
                    )
                    return JSONResponse(
                        status_code=429,
                        content=_error_body(
                            "too_many_requests",
                            message,
                            {
                                "bucket": bucket,
                                "limit": limit,
                                "window_seconds": int(WINDOW_SECONDS),
                                "retry_after": round(retry_after, 3),
                            },
                        ),
                        headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
                    )
        return await call_next(request)

    @app.middleware("http")
    async def write_auth(request: Request, call_next):
        """可选的写操作共享 token（issue #17.1）。

        `TERRARIA_API_TOKEN` 为空时不启用（边缘已有 Cloudflare Access / VPN 的
        部署可以留空）。启用后只拦写方法，GET 与 `/api/meta` 不受影响。
        """
        settings = resolve_runtime().settings
        if settings.api_token and request.method in _UNSAFE_METHODS:
            supplied = _bearer_token(request.headers.get("authorization"))
            if not supplied:
                supplied = request.headers.get("x-api-token", "")
            if not hmac.compare_digest(supplied, settings.api_token):
                return JSONResponse(
                    status_code=401,
                    content=_error_body(
                        "unauthorized",
                        "写操作需要提供 Authorization: Bearer <token>",
                    ),
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    @app.middleware("http")
    async def client_version_gate(request: Request, call_next):
        """低于 `min_client_version` 的面板直接拒绝（issue #17.3）。

        不带 `X-Client-Version` 的调用方（脚本、curl）不受影响；`/api/meta` 与
        `/api/health` 永远放行，好让老面板能通过握手知道自己该升级。
        """
        client_version = request.headers.get("X-Client-Version")
        if (
            client_version
            and looks_like_version(client_version)
            and request.method != "OPTIONS"
            and request.url.path not in _VERSION_EXEMPT_PATHS
        ):
            settings = resolve_runtime().settings
            if version_lt(client_version, settings.min_client_version):
                return JSONResponse(
                    status_code=426,
                    content=_error_body(
                        "client_outdated",
                        f"面板版本 {client_version} 过旧，"
                        f"至少需要 {settings.min_client_version}",
                        {
                            "client_version": client_version,
                            "min_client_version": settings.min_client_version,
                        },
                    ),
                )
        return await call_next(request)

    # 放在这里（而不是函数开头）是为了让 CORSMiddleware 位于上面所有早退中间件的
    # 外层：413/429/401/426 响应都能带上 access-control-allow-origin。
    bootstrap = runtime.settings if runtime is not None else Settings()
    cors_origins = [
        origin.strip() for origin in bootstrap.cors_origins.split(",") if origin.strip()
    ] or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        # 面板要读回显的 X-Client-Version，必须显式 expose（issue #17.3）
        expose_headers=["X-Client-Version"],
    )

    @app.middleware("http")
    async def client_version_header(request: Request, call_next):
        """回显 `X-Client-Version`，方便面板确认自己连的是哪个后端。

        它在最外层，所以 413/429/401/426 这些早退响应也会带上回显头。
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
