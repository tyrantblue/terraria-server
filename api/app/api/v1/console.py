"""v1：结构化控制台。

旧接口返回的是日志原文（含大量状态横幅），前端只能自己写正则去猜每行是什么。
v1 给每行附上 `kind`（player_join / chat / world_save / startup / error …），
并支持按 `since` 游标增量拉取；WebSocket 推的是 JSON 事件而不是裸文本。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.api.deps import RuntimeDep, runtime
from app.schemas.v1 import (
    AuditResponse,
    CommandRequest,
    CommandResponse,
    ConsoleResponse,
)
from app.services.console.audit import TAIL_MAX, guard_command
from app.services.console.parser import classify_line, is_fence_line, split_line
from app.services.runtime import Runtime

router = APIRouter(prefix="/api/v1", tags=["v1:console"])

POLL_INTERVAL = 0.25
INITIAL_LINES = 100


def _view(offset: int, text: str) -> dict[str, object]:
    stamp, _body = split_line(text)
    return {"offset": offset, "ts": stamp, "kind": classify_line(text), "text": text}


def _line_event(offset: int, text: str) -> dict[str, object]:
    """WS 帧：与 REST 的 ConsoleLine 同形，另加 `type`。

    回放（历史）与实时使用**同一个真实 `offset`**（见 issue #7）：
    REST 侧的 offset 是稳定的行标识/增量游标，前端拿它做去重键与 React key
    才不会把 N 行折叠成 1 行。
    """
    stamp, _body = split_line(text)
    return {
        "type": "console.line",
        "offset": offset,
        "ts": stamp,
        "kind": classify_line(text),
        "text": text,
    }


@router.get("/console", response_model=ConsoleResponse)
def console(
    rt: RuntimeDep,
    tail: int = Query(default=200, ge=1, le=1000),
    since: int | None = Query(default=None, ge=0),
) -> dict[str, object]:
    """`since` 缺省时返回最后 tail 行；给了 since 就返回该字节偏移之后的新行。"""
    if since is not None:
        cursor, lines = rt.reader.read_lines_with_offsets(since)
        return {
            "lines": [
                _view(offset, text)
                for offset, text in lines
                if not is_fence_line(text)
            ],
            "cursor": cursor,
        }

    entries = rt.reader.tail_with_offsets(tail)
    return {
        "lines": [
            _view(offset, text) for offset, text in entries if not is_fence_line(text)
        ],
        "cursor": rt.reader.size(),
    }


@router.post("/console/commands", response_model=CommandResponse)
def run_command(request: CommandRequest, rt: RuntimeDep) -> dict[str, object]:
    command = guard_command(request.command)
    try:
        output = rt.channel.run(command)
    except Exception as exc:  # noqa: BLE001 - 失败的尝试同样要留痕
        rt.audit.record(command, actor="api", result=f"failed: {type(exc).__name__}")
        raise
    rt.audit.record(command, actor="api", result=output)
    rt.status.invalidate()
    return {"ok": True, "command": command, "output": output}


@router.get("/console/audit", response_model=AuditResponse)
def audit(
    rt: RuntimeDep,
    tail: int | None = Query(default=None, ge=1, le=TAIL_MAX),
) -> dict[str, object]:
    """默认返回内存里的最近 200 条；带 `?tail=N` 时从 `control/audit.log` 回读。

    落盘之后 API 重启不再丢失审计（issue #3）。
    """
    return {"entries": [entry.as_dict() for entry in rt.audit.entries(tail)]}


@router.websocket("/console/stream")
async def stream(websocket: WebSocket, rt: Runtime = Depends(runtime)) -> None:
    """先回放最近 INITIAL_LINES 行（带真实 offset），再推 `hello`，然后持续推新行。

    顺序与游标的取法有讲究：

    * `cursor` 在回放**之前**取，回放里 `offset >= cursor` 的行跳过——
      它们属于「回放期间新增」，留给下面的实时循环发，避免重复也避免丢行；
    * `hello` 仍然最后发，`hello.cursor` 是权威续传点：前端断线重连时
      用 REST `?since=hello.cursor` 补洞即可。
    """
    await websocket.accept()
    reader = rt.reader
    try:
        cursor = reader.size()
        for offset, text in reader.tail_with_offsets(INITIAL_LINES):
            if is_fence_line(text) or offset >= cursor:
                continue
            await websocket.send_json(_line_event(offset, text))
        await websocket.send_json({"type": "hello", "cursor": cursor})

        while True:
            if reader.size() < cursor:
                cursor = 0
            cursor, lines = reader.read_lines_with_offsets(cursor)
            for offset, text in lines:
                if is_fence_line(text):
                    continue
                await websocket.send_json(_line_event(offset, text))
            await asyncio.sleep(POLL_INTERVAL)
    except WebSocketDisconnect:
        return
