"""v1：连接守卫可视化（白名单 / 封禁 / 手动操作）。

守卫容器负责真正的 iptables/ipset，这里通过文件 IPC 读写它的状态（见
services/guard_client.py）。守卫没运行时返回 503 并说明原因，而不是假装成功。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import RuntimeDep
from app.core.errors import AppError
from app.schemas.v1 import (
    GuardActionResult,
    GuardBanRequest,
    GuardIpRequest,
    GuardState,
)
from app.services.guard_client import GuardUnavailable

router = APIRouter(prefix="/api/v1", tags=["v1:guard"])


class GuardDown(AppError):
    status_code = 503
    code = "guard_unavailable"


def _guard_state(rt: RuntimeDep, *, allow_unavailable: bool = False) -> dict[str, object]:
    try:
        state = rt.guard.state()
    except GuardUnavailable as exc:
        if allow_unavailable:
            return {
                "available": False,
                "stale": True,
                "age": 0.0,
                "updated_at": None,
                "port": None,
                "allowlist_only": False,
                "allow": [],
                "banned": [],
                "counters": {},
            }
        raise GuardDown(str(exc)) from exc

    return {
        "available": not state.get("stale"),
        "stale": bool(state.get("stale")),
        "age": float(state.get("age") or 0.0),
        "updated_at": state.get("updated_at"),
        "port": state.get("port"),
        "allowlist_only": bool(state.get("allowlist_only")),
        "allow": state.get("allow") or [],
        "banned": state.get("banned") or [],
        "counters": state.get("counters") or {},
    }


@router.get("/guard", response_model=GuardState)
def get_guard(rt: RuntimeDep) -> dict[str, object]:
    """即使守卫不在也返回 200（available=false），方便面板显示"守卫已停止"。"""
    return _guard_state(rt, allow_unavailable=True)


def _submit(rt: RuntimeDep, action: str, args: dict[str, object]) -> dict[str, object]:
    try:
        return rt.guard.submit(action, args)
    except GuardUnavailable as exc:
        raise GuardDown(str(exc)) from exc


@router.post("/guard/bans", response_model=GuardActionResult)
def ban_ip(request: GuardBanRequest, rt: RuntimeDep) -> dict[str, object]:
    args: dict[str, object] = {"ip": request.ip}
    if request.seconds:
        args["seconds"] = request.seconds
    return _submit(rt, "ban", args)


@router.delete("/guard/bans/{ip}", response_model=GuardActionResult)
def unban_ip(ip: str, rt: RuntimeDep) -> dict[str, object]:
    return _submit(rt, "unban", {"ip": ip})


@router.post("/guard/allow", response_model=GuardActionResult)
def allow_ip(request: GuardIpRequest, rt: RuntimeDep) -> dict[str, object]:
    """加入静态白名单：写进 guard/allow.txt 并立即对 tg_allow 生效（重启不丢）。"""
    return _submit(rt, "allow", {"ip": request.ip})


@router.delete("/guard/allow/{ip}", response_model=GuardActionResult)
def disallow_ip(ip: str, rt: RuntimeDep) -> dict[str, object]:
    """移出白名单（静态与学习型都清），如果已不在任何名单里就只做解封。"""
    return _submit(rt, "disallow", {"ip": ip})


@router.post("/guard/reload", response_model=GuardActionResult)
def reload_guard(rt: RuntimeDep) -> dict[str, object]:
    """按两个白名单文件重新同步 tg_allow（手工改过文件后用它）。"""
    return _submit(rt, "reload", {})
