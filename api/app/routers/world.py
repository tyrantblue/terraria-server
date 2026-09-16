from datetime import datetime
from pathlib import Path
import time

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.services.terraria import send_command, execute_command


router = APIRouter(prefix="/api/world", tags=["world"])

WORLDS_DIR = Path("/opt/terraria/worlds")
CONFIG_FILE = Path("/opt/terraria/config/serverconfig.txt")


class SwitchWorldRequest(BaseModel):
    file: str


@router.get("/list")
def list_worlds():
    config_world = None

    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()

            if line.startswith("world="):
                config_world = Path(line.split("=", 1)[1]).name
                break

    worlds = []

    for path in sorted(WORLDS_DIR.glob("*.wld")):
        stat = path.stat()

        worlds.append({
            "name": path.stem,
            "file": path.name,
            "size": stat.st_size,
            "modified_at": stat.st_mtime,
            "active": path.name == config_world,
        })

    return {
        "worlds": worlds,
        "active_world": config_world,
    }


@router.post("/upload")
async def upload_world(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="filename is required",
        )

    filename = Path(file.filename).name

    if not filename.lower().endswith(".wld"):
        raise HTTPException(
            status_code=400,
            detail="only .wld files are allowed",
        )

    destination = WORLDS_DIR / filename

    if destination.exists():
        raise HTTPException(
            status_code=409,
            detail=f"world already exists: {filename}",
        )

    with destination.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)

    return {
        "success": True,
        "name": destination.stem,
        "file": destination.name,
        "size": destination.stat().st_size,
    }


@router.post("/switch")
def switch_world(request: SwitchWorldRequest):
    filename = Path(request.file).name

    if filename != request.file:
        raise HTTPException(
            status_code=400,
            detail="invalid filename",
        )

    if not filename.lower().endswith(".wld"):
        raise HTTPException(
            status_code=400,
            detail="only .wld files are allowed",
        )

    world_file = WORLDS_DIR / filename

    if not world_file.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"world not found: {filename}",
        )

    # 1. 先保存当前世界
    try:
        send_command("save")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"failed to save world: {e}",
        )

    # 给 Terraria 一点时间完成保存
    time.sleep(1)

    # 2. 修改 serverconfig.txt
    if not CONFIG_FILE.exists():
        raise HTTPException(
            status_code=500,
            detail="server config file not found",
        )

    lines = CONFIG_FILE.read_text(
        encoding="utf-8"
    ).splitlines()

    found_world = False
    new_lines = []

    for line in lines:
        if line.strip().startswith("world="):
            new_lines.append(f"world=/worlds/{filename}")
            found_world = True
        else:
            new_lines.append(line)

    if not found_world:
        new_lines.append(f"world=/worlds/{filename}")

    CONFIG_FILE.write_text(
        "\n".join(new_lines) + "\n",
        encoding="utf-8",
    )

    # 3. 退出 Terraria
    try:
        send_command("exit")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"failed to stop server: {e}",
        )

    # 4. 等待 Terraria 自动重启
    deadline = time.monotonic() + 30

    while time.monotonic() < deadline:
        try:
            output = execute_command("version")

            if "Terraria Server" in output:
                break

        except Exception:
            pass

        time.sleep(1)

    else:
        raise HTTPException(
            status_code=504,
            detail="Terraria server did not restart within 30 seconds",
        )

    return {
        "success": True,
        "world": filename,
        "message": "world switched successfully",
    }
