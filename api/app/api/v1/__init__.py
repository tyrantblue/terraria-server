"""API v1：资源导向的接口面。

2.0.0 起旧 `/api/*` 资源路由已删除（现在 404），这里是唯一的接口面；
系统级接口（`/api/health`、`/api/meta`）仍注册在 `app/api/system.py`。
"""

from app.api.v1.router import api_v1

__all__ = ["api_v1"]
