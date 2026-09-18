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


class OperationKindInfo(BaseModel):
    """后端自己发布自己的词汇表，面板不必硬编码 kind → 文案（issue #18）。"""

    kind: str
    label: str


class MetaResponse(BaseModel):
    api_version: str
    min_client_version: str
    server_version: str | None = None
    capabilities: list[str]
    #: 每项能力的「引入版本」，老后端没有的能力面板据此写「需要 API ≥ x.y」（issue #14）
    capability_since: dict[str, str] = Field(default_factory=dict)
    #: 所有 known operation kind 及人读标签
    operation_kinds: list[OperationKindInfo] = Field(default_factory=list)
    #: 这些 kind 同一时刻只允许一个（见 v1.md §7 的互斥说明）
    exclusive_operation_kinds: list[str] = Field(default_factory=list)
    deprecations: list[dict[str, Any]] = Field(default_factory=list)
    links: MetaLinks
