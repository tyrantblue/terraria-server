"""API v1：资源导向的新接口面。旧接口仍然可用（见 core/deprecations.py）。"""

from app.api.v1.router import api_v1

__all__ = ["api_v1"]
