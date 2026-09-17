"""控制台 WebSocket。

与旧实现相比只有两点变化：
* 用 LogReader 按偏移读完整行（不再把半行发出去）；
* 过滤掉哨兵命令的回显（`Usage: kick <player>`，见 services/console/parser.py）。
对外仍然是「一条日志一行文本」，路径与握手方式不变。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.api.deps import runtime
from app.services.console.parser import is_fence_line
from app.services.runtime import Runtime

router = APIRouter(prefix="/api/server", tags=["console"])

INITIAL_LINES = 100
POLL_INTERVAL = 0.25


@router.websocket("/ws")
async def console_websocket(
    websocket: WebSocket,
    rt: Runtime = Depends(runtime),
) -> None:
    await websocket.accept()
    reader = rt.reader
    try:
        for text in reader.tail(INITIAL_LINES):
            if not is_fence_line(text):
                await websocket.send_text(text)
        offset = reader.size()

        while True:
            if reader.size() < offset:  # 日志被轮转/截断
                offset = 0
            offset, lines = reader.read_lines(offset)
            for text in lines:
                if not is_fence_line(text):
                    await websocket.send_text(text)
            await asyncio.sleep(POLL_INTERVAL)
    except WebSocketDisconnect:
        return
