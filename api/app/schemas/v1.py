"""`/api/v1` 的出入参模型。

v1 与旧接口的区别（详见 docs/api/v1.md）：
* 资源导向：`/players/{name}/kick` 而不是 `POST /kick {player}`；
* 成功返回资源本体，不再包 `{"success": true}`；
* 读用 GET，查询不再用 POST；
* 长耗时操作返回 `202 + operation_id`；
* 明确区分「运行时」与「持久配置」。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- 通用
class OkResponse(BaseModel):
    ok: bool = True


class OperationRef(BaseModel):
    """202 响应：去 GET /api/v1/operations/{id} 查进度。"""

    operation_id: str
    state: str
    kind: str
    poll: str = "/api/v1/operations/{operation_id}"


class OperationView(BaseModel):
    id: str
    kind: str
    state: str
    progress: int
    message: str
    created_at: float
    started_at: float | None
    finished_at: float | None
    result: dict[str, Any] | None
    error: str | None


class OperationList(BaseModel):
    operations: list[OperationView]


# ---------------------------------------------------------------- server
class PlayerEntryView(BaseModel):
    name: str
    ip: str
    port: int


class PlayerCollection(BaseModel):
    online: int
    max: int | None
    players: list[PlayerEntryView]


class WorldRef(BaseModel):
    file: str
    name: str
    size: int
    modified_at: float
    active: bool


class ConfigView(BaseModel):
    """serverconfig.txt 里可编辑的键（world 已归一化成文件名）。"""

    values: dict[str, str]
    editable_keys: list[str]
    runtime_keys: list[str]
    restart_keys: list[str]
    path: str


class ServerState(BaseModel):
    running: bool
    version: str | None
    port: int | None
    max_players: int | None
    time: str | None
    seed: str | None
    motd: str | None
    players: PlayerCollection
    world: WorldRef | None
    config: dict[str, str]


class ActionRequest(BaseModel):
    action: Literal["save", "settle"]


class TimeRequest(BaseModel):
    phase: Literal["dawn", "noon", "dusk", "midnight"]


class TimeResponse(BaseModel):
    phase: str
    time: str | None


# ---------------------------------------------------------------- players
class BroadcastRequest(BaseModel):
    message: str


class BanListResponse(BaseModel):
    bans: list[str]
    source: str
    exists: bool
    note: str | None = None


# ---------------------------------------------------------------- console
class ConsoleLine(BaseModel):
    offset: int
    kind: str
    text: str


class ConsoleResponse(BaseModel):
    lines: list[ConsoleLine]
    cursor: int


class CommandRequest(BaseModel):
    command: str


class CommandResponse(BaseModel):
    ok: bool = True
    command: str
    output: str


class AuditEntry(BaseModel):
    ts: float
    command: str
    actor: str


class AuditResponse(BaseModel):
    entries: list[AuditEntry]


# ---------------------------------------------------------------- config
class ConfigUpdateRequest(BaseModel):
    # 具体键在 services/config_service.py 的白名单里校验
    values: dict[str, Any]
    apply: bool = False
    confirm_low_max_players: bool = False


class ConfigUpdateResponse(BaseModel):
    persisted: list[str]
    changed: list[str]
    applied: list[str]
    requires_restart: list[str]
    operation_id: str | None = None


# ---------------------------------------------------------------- worlds
class WorldListResponse(BaseModel):
    worlds: list[WorldRef]
    active_world: str | None
    backup_dir: str


class BackupEntry(BaseModel):
    name: str
    created_at: float
    files: int
    size: int


class BackupListResponse(BaseModel):
    backups: list[BackupEntry]


class UploadResponse(BaseModel):
    name: str
    file: str
    size: int


# ---------------------------------------------------------------- meta
class UsageEntry(BaseModel):
    path: str
    count: int
    first_seen: float
    last_seen: float
    client_versions: dict[str, int]


class UsageResponse(BaseModel):
    note: str
    usage: list[UsageEntry]
