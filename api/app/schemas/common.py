"""通用响应模型。

2.0.0 删掉了旧接口专用的 `SuccessResponse` / `CommandResponse`
（`{"success": true}` 那套包装只剩历史意义，见 docs/api/CHANGELOG.md）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """统一错误体：`detail` 兼容旧前端，`error` 供新前端 switch code。"""

    detail: str
    error: ErrorBody


class HealthResponse(BaseModel):
    status: str
    service: str


class MetaLinks(BaseModel):
    openapi: str
    docs: str
    changelog: str


class MetaResponse(BaseModel):
    api_version: str
    min_client_version: str
    server_version: str | None = None
    capabilities: list[str]
    deprecations: list[dict[str, Any]] = Field(default_factory=list)
    links: MetaLinks
