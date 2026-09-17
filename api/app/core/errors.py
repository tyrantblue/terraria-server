"""领域异常 + 统一错误响应。

契约（见 docs/api-refactor-plan.md §4.2）：
    失败时返回 {"detail": "<message>", "error": {"code", "message", "details?"}}

`detail` 是为兼容现有前端保留的旧字段，`error` 是新增的机器可读结构。
两者同时存在，旧前端只读 `detail` 不会受影响。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有领域异常的基类。router 里不再直接抛 HTTPException。"""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code

    def to_body(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        # detail 放在前面，保证 JSON 里的字段顺序与旧响应一致
        return {"detail": self.message, "error": error}


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"


class ValidationFailed(AppError):
    """参数本身合法（通过了 pydantic），但不符合业务规则。"""

    status_code = 422
    code = "validation_failed"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class UpstreamFailed(AppError):
    """Terraria 服务端返回了不符合预期的结果。"""

    status_code = 502
    code = "upstream_failed"


class ConsoleUnavailable(AppError):
    """写不进控制 FIFO：服务端正在重启 / 容器不在。"""

    status_code = 503
    code = "console_unavailable"


class ConsoleBusy(ConsoleUnavailable):
    """没能在超时内拿到控制台锁。"""

    code = "console_busy"


class ConsoleTimeout(ConsoleUnavailable):
    """命令回显超时（哨兵没出现）。"""

    code = "console_timeout"
