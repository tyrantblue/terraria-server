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
from app.services.console.audit import guard_command
from app.services.console.parser import classify_line, is_fence_line
from app.services.runtime import Runtime

router = APIRouter(prefix="/api/v1", tags=["v1:console"])

POLL_INTERVAL = 0.25
INITIAL_LINES = 100


def _view(offset: int, text: str) -> dict[str, object]:
    return {"offset": offset, "kind": classify_line(text), "text": text}


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
    output = rt.channel.run(command)
    rt.audit.record(command, actor="api")
    rt.status.invalidate()
    return {"ok": True, "command": command, "output": output}


@router.get("/console/audit", response_model=AuditResponse)
def audit(rt: RuntimeDep) -> dict[str, object]:
    return {
        "entries": [
            {"ts": entry.ts, "command": entry.command, "actor": entry.actor}
            for entry in rt.audit.entries()
        ]
    }


@router.websocket("/console/stream")
async def stream(websocket: WebSocket, rt: Runtime = Depends(runtime)) -> None:
    await websocket.accept()
    reader = rt.reader
    try:
        for text in reader.tail(INITIAL_LINES):
            if not is_fence_line(text):
                await websocket.send_json(
                    {"type": "console.line", "offset": -1, "kind": classify_line(text), "text": text}
                )
        cursor = reader.size()
        await websocket.send_json({"type": "hello", "cursor": cursor})

        while True:
            if reader.size() < cursor:
                cursor = 0
            cursor, lines = reader.read_lines_with_offsets(cursor)
            for offset, text in lines:
                if is_fence_line(text):
                    continue
                await websocket.send_json(
                    {"type": "console.line", "offset": offset, "kind": classify_line(text), "text": text}
                )
            await asyncio.sleep(POLL_INTERVAL)
    except WebSocketDisconnect:
        return
