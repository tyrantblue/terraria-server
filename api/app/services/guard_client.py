"""与守卫进程的客户端（文件 IPC）。

守卫容器拿着 ipset/NET_ADMIN 权限，API 容器没有、也不该有（它是公网暴露面）。
两边共享 `control/` 挂载，所以用文件通信：

    control/guard-state.json    守卫写、API 读（白名单/封禁/计数器快照）
    control/guard-commands.jsonl API 追加、守卫消费（一行一条 JSON）

API 提交命令后轮询状态文件里的 results[id]，拿到结果再返回给前端——
对前端来说就是普通的同步接口，不需要知道背后的文件协议。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

from app.core.errors import BadRequest

logger = logging.getLogger(__name__)

#: 状态多久没更新就算守卫不在（秒）
STALE_AFTER = 30.0
IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def is_valid_ipv4(value: str) -> bool:
    """点分十进制 IPv4，且每段 0..255。"""
    if not IP_RE.match(value):
        return False
    return all(0 <= int(part) <= 255 for part in value.split("."))


class GuardUnavailable(Exception):
    """守卫没在运行，或者状态文件过期。"""


class GuardClient:
    def __init__(
        self,
        state_file: Path,
        command_file: Path,
        *,
        stale_after: float = STALE_AFTER,
    ) -> None:
        self.state_file = state_file
        self.command_file = command_file
        self.stale_after = stale_after

    # -- 读 -----------------------------------------------------------
    def state(self) -> dict[str, object]:
        try:
            raw = self.state_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise GuardUnavailable(
                "读不到 guard-state.json：守卫容器可能没在运行"
            ) from exc
        try:
            state = json.loads(raw)
        except ValueError as exc:
            raise GuardUnavailable("guard-state.json 内容损坏") from exc
        updated = float(state.get("updated_at") or 0)
        state["stale"] = (time.time() - updated) > self.stale_after
        state["age"] = max(0.0, time.time() - updated)
        return state

    # -- 写 -----------------------------------------------------------
    def submit(
        self,
        action: str,
        args: dict[str, object] | None = None,
        *,
        timeout: float = 4.0,
    ) -> dict[str, object]:
        args = args or {}
        ip = str(args.get("ip") or "").strip()
        if action != "reload":
            # 输入非法是调用方的错（400），只有守卫进程/控制文件不可用才是 503。
            # 以前两种都抛 GuardUnavailable，面板会把「IP 打错了」显示成
            # 「守卫没在运行」，让人去排查一个不存在的问题（issue #16.4）。
            if not ip:
                raise BadRequest("缺少 ip", details={"ip": ip})
            if not is_valid_ipv4(ip):
                raise BadRequest(f"ip 格式不对: {ip}", details={"ip": ip})

        command_id = uuid.uuid4().hex[:12]
        payload = {
            "id": command_id,
            "action": action,
            "args": args,
            "ts": time.time(),
        }
        try:
            with self.command_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise GuardUnavailable(f"写命令失败: {exc}") from exc

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                state = self.state()
            except GuardUnavailable:
                time.sleep(0.2)
                continue
            result = (state.get("results") or {}).get(command_id)
            if isinstance(result, dict):
                return {
                    "ok": bool(result.get("ok")),
                    "message": str(result.get("message") or ""),
                    "command_id": command_id,
                }
            time.sleep(0.2)

        raise GuardUnavailable(
            "守卫没有在预期时间内确认命令（容器在运行吗？看 docker compose logs terraria-guard）"
        )
