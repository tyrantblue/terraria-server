"""通用响应模型。

成功响应保持与旧实现完全一致的字段（含 `success: true`）；
错误响应见 core/errors.py 与 schemas/error 段落。
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


class SuccessResponse(BaseModel):
    success: bool = True


class CommandResponse(SuccessResponse):
    command: str


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
