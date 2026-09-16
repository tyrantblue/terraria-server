
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.terraria import (
    send_command,
    get_recent_output,
    execute_command,
)


router = APIRouter(
    prefix="/api/server",
    tags=["server"],
)


class CommandRequest(BaseModel):
    command: str


class PlayerRequest(BaseModel):
    player: str


class MessageRequest(BaseModel):
    message: str


class MotdRequest(BaseModel):
    motd: str


class PasswordRequest(BaseModel):
    password: str


class MaxPlayersRequest(BaseModel):
    max_players: int = Field(
        ge=1,
        le=255,
    )


def execute(command: str):
    try:
        send_command(command)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


def get_command_output(
    command: str,
) -> str:
    try:
        return execute_command(command)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


def parse_players(
    output: str,
) -> list[str]:
    players = []

    for line in output.splitlines():
        line = line.strip()

        if not line:
            continue

        if "No players connected." in line:
            continue

        # Terraria 1.4.5.x:
        # PlayerName (IP:Port)
        match = re.match(
            r"^(.+?) \(.+:\d+\)$",
            line,
        )

        if match:
            players.append(
                match.group(1).strip()
            )

    return players


@router.post("/command")
def command(
    request: CommandRequest,
):
    execute(request.command)

    return {
        "success": True,
        "command": request.command,
    }


@router.get("/console")
def console():
    return {
        "lines": get_recent_output(),
    }


@router.get("/players")
def players():
    output = get_command_output("playing")

    players = parse_players(output)

    return {
        "online": len(players),
        "players": players,
    }


@router.get("/status")
def status():
    version_output = get_command_output(
        "version"
    )

    port_output = get_command_output(
        "port"
    )

    maxplayers_output = get_command_output(
        "maxplayers"
    )

    time_output = get_command_output(
        "time"
    )

    seed_output = get_command_output(
        "seed"
    )

    motd_output = get_command_output(
        "motd"
    )

    players_output = get_command_output(
        "playing"
    )

    version = None
    port = None
    max_players = None
    game_time = None
    seed = None
    motd = None

    version_match = re.search(
        r"Terraria Server v(.+)",
        version_output,
    )

    if version_match:
        version = version_match.group(1).strip()

    port_match = re.search(
        r"Port:\s*(\d+)",
        port_output,
    )

    if port_match:
        port = int(
            port_match.group(1)
        )

    maxplayers_match = re.search(
        r"Player limit:\s*(\d+)",
        maxplayers_output,
    )

    if maxplayers_match:
        max_players = int(
            maxplayers_match.group(1)
        )

    time_match = re.search(
        r"Time:\s*(.+)",
        time_output,
    )

    if time_match:
        game_time = time_match.group(1).strip()

    seed_match = re.search(
        r"World Seed:\s*(.+)",
        seed_output,
    )

    if seed_match:
        seed = seed_match.group(1).strip()

    motd_match = re.search(
        r"MOTD:\s*(.*)",
        motd_output,
    )

    if motd_match:
        motd = motd_match.group(1).strip()

    players = parse_players(
        players_output
    )

    return {
        "running": True,
        "version": version,
        "port": port,
        "max_players": max_players,
        "time": game_time,
        "seed": seed,
        "motd": motd,
        "players": {
            "online": len(players),
            "list": players,
        },
    }


@router.post("/playing")
def playing():
    execute("playing")

    return {
        "success": True,
        "command": "playing",
    }


@router.post("/version")
def version():
    execute("version")

    return {
        "success": True,
        "command": "version",
    }


@router.post("/port")
def port():
    execute("port")

    return {
        "success": True,
        "command": "port",
    }


@router.post("/maxplayers")
def maxplayers(
    request: MaxPlayersRequest,
):
    execute(
        f"maxplayers {request.max_players}"
    )

    return {
        "success": True,
        "max_players": request.max_players,
    }


@router.post("/save")
def save():
    execute("save")

    return {
        "success": True,
        "command": "save",
    }


@router.post("/settle")
def settle():
    execute("settle")

    return {
        "success": True,
        "command": "settle",
    }


@router.post("/time/dawn")
def dawn():
    execute("dawn")

    return {
        "success": True,
        "command": "dawn",
    }


@router.post("/time/noon")
def noon():
    execute("noon")

    return {
        "success": True,
        "command": "noon",
    }


@router.post("/time/dusk")
def dusk():
    execute("dusk")

    return {
        "success": True,
        "command": "dusk",
    }


@router.post("/time/midnight")
def midnight():
    execute("midnight")

    return {
        "success": True,
        "command": "midnight",
    }


@router.post("/say")
def say(
    request: MessageRequest,
):
    message = request.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="message cannot be empty",
        )

    if "\n" in message or "\r" in message:
        raise HTTPException(
            status_code=400,
            detail="message must be a single line",
        )

    execute(f"say {message}")

    return {
        "success": True,
        "message": message,
    }


@router.post("/kick")
def kick(
    request: PlayerRequest,
):
    player = request.player.strip()

    if not player:
        raise HTTPException(
            status_code=400,
            detail="player cannot be empty",
        )

    if "\n" in player or "\r" in player:
        raise HTTPException(
            status_code=400,
            detail="player must be a single line",
        )

    execute(f"kick {player}")

    return {
        "success": True,
        "player": player,
    }


@router.post("/ban")
def ban(
    request: PlayerRequest,
):
    player = request.player.strip()

    if not player:
        raise HTTPException(
            status_code=400,
            detail="player cannot be empty",
        )

    if "\n" in player or "\r" in player:
        raise HTTPException(
            status_code=400,
            detail="player must be a single line",
        )

    execute(f"ban {player}")

    return {
        "success": True,
        "player": player,
    }


@router.post("/motd")
def motd(
    request: MotdRequest,
):
    value = request.motd.strip()

    if not value:
        raise HTTPException(
            status_code=400,
            detail="motd cannot be empty",
        )

    if "\n" in value or "\r" in value:
        raise HTTPException(
            status_code=400,
            detail="motd must be a single line",
        )

    execute(f"motd {value}")

    return {
        "success": True,
        "motd": value,
    }


@router.post("/password")
def password(
    request: PasswordRequest,
):
    password = request.password

    if "\n" in password or "\r" in password:
        raise HTTPException(
            status_code=400,
            detail="password must be a single line",
        )

    execute(f"password {password}")

    return {
        "success": True,
    }
