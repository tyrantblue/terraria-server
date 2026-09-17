"""FastAPI 依赖：把 Runtime 暴露给路由层。

路由只依赖这里，不直接 new 任何 service，因此测试可以用
`app.dependency_overrides[runtime]` 整体替换成假的 Terraria 环境。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.services.runtime import Runtime, get_runtime


def runtime() -> Runtime:
    return get_runtime()


RuntimeDep = Annotated[Runtime, Depends(runtime)]
