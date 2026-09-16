import asyncio
from pathlib import Path

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect


router = APIRouter(
    prefix="/api/server",
    tags=["console"],
)


LOG_FILE = Path(
    "/opt/terraria/control/output.log"
)


@router.websocket("/ws")
async def console_websocket(
    websocket: WebSocket,
):
    await websocket.accept()

    try:
        # 连接时先发送最近的日志
        if LOG_FILE.exists():
            initial_lines = get_recent_lines(100)

            for line in initial_lines:
                await websocket.send_text(line)

            previous_size = LOG_FILE.stat().st_size
        else:
            previous_size = 0

        # 持续监听日志文件
        while True:

            if not LOG_FILE.exists():
                await asyncio.sleep(0.25)
                continue

            current_size = LOG_FILE.stat().st_size

            # 日志文件被清空/重建
            if current_size < previous_size:
                previous_size = 0

            # 有新内容
            if current_size > previous_size:
                new_output = read_new_output(
                    previous_size,
                )

                previous_size = current_size

                if new_output:
                    await websocket.send_text(
                        new_output,
                    )

            await asyncio.sleep(0.25)

    except WebSocketDisconnect:
        pass


def get_recent_lines(
    count: int,
) -> list[str]:
    if not LOG_FILE.exists():
        return []

    with LOG_FILE.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as f:
        return [
            line.rstrip("\n")
            for line in f.readlines()[-count:]
        ]


def read_new_output(
    previous_size: int,
) -> str:
    if not LOG_FILE.exists():
        return ""

    with LOG_FILE.open(
        "rb",
    ) as f:
        f.seek(previous_size)

        data = f.read()

    if not data:
        return ""

    return data.decode(
        "utf-8",
        errors="replace",
    )
