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
    """202 响应：去 `poll` 指向的地址查进度。

    `poll` 是**按请求生成的真实 URL**（如 `/api/v1/operations/8f3c…`），
    不再是未替换的模板字符串 `/api/v1/operations/{operation_id}`（issue #16.2）。
    面板也可以继续用 `operation_id` 自己拼。
    """

    operation_id: str
    state: str
    kind: str
    poll: str


def operation_ref(operation: Any) -> dict[str, Any]:
    """把 Operation 转成 202 响应体，并生成真实的 `poll` URL（issue #16.2）。"""
    return {
        "operation_id": operation.id,
        "state": operation.state,
        "kind": operation.kind,
        "poll": f"/api/v1/operations/{operation.id}",
    }


class OperationView(BaseModel):
    """`message` 是中文自由文本回退；程序判断请用 `message_code` / `state`（issue #18）。"""

    id: str
    kind: str
    #: kind 的人读标签（后端发布词汇表，面板不必硬编码映射）
    kind_label: str
    state: str
    progress: int
    message: str
    #: 稳定标识，如 `restart.stopping_server`；pending/running/succeeded/failed 也有值
    message_code: str
    #: 与 message_code 配套的参数，如 {"file": "gogogo.wld"}
    message_params: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    started_at: float | None
    finished_at: float | None
    result: dict[str, Any] | None
    error: str | None


class OperationList(BaseModel):
    operations: list[OperationView]
    #: 还在 pending/running 的数量——Dashboard 不必拉全量再自己数
    in_flight: int = 0


# ---------------------------------------------------------------- server
class PlayerEntryView(BaseModel):
    name: str
    ip: str
    port: int


class PlayerCollection(BaseModel):
    online: int
    max: int | None
    players: list[PlayerEntryView]


class WorldMetadataView(BaseModel):
    """从 `.wld` 头部解析出来的元数据（解析失败时整个对象为 `null`）。"""

    format_version: int
    size_tier: str                       # small | medium | large
    width: int
    height: int
    difficulty: str                      # classic | expert | master | journey
    created_at: str | None               # ISO-8601（UTC）；拿不到时为 null


class WorldRef(BaseModel):
    file: str
    name: str
    size: int
    modified_at: float
    active: bool
    #: 解析失败/旧格式/损坏文件时为 null，接口不会因此失败
    metadata: WorldMetadataView | None


class ConfigView(BaseModel):
    """serverconfig.txt 里可编辑的键（world 已归一化成文件名）。

    `password` 这类敏感键**不回显明文**：值被替换成掩码（`••••••`），
    是否已设置用 `password_set` 表达。写入（PUT）语义不变，仍然收明文。
    """

    values: dict[str, str]
    password_set: bool
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
    #: 与 ConfigView.values 同源：敏感键已掩码
    config: dict[str, str]
    password_set: bool
    #: 日志管道健康（issue #2）：控制台哨兵没回显，或日志长时间没更新
    log_stalled: bool
    #: 日志最后一次写入距今多少秒（文件不存在时为 null）
    log_age: float | None


# ---------------------------------------------------------------- metrics
class MetricPoint(BaseModel):
    """一个采样点：容器资源 + 磁盘 + 在线人数。读不到的字段为 null。"""

    ts: float
    cpu_percent: float | None
    cpu_cores: float | None
    memory_bytes: int | None
    memory_limit_bytes: int | None
    disk_free_bytes: int | None
    disk_total_bytes: int | None
    players_online: int | None


class MetricsResponse(BaseModel):
    interval_seconds: float
    retention_points: int
    window_minutes: float
    latest: MetricPoint | None
    points: list[MetricPoint]


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
    #: 由 start.sh 打上的行首时间戳解析而来；旧日志没有则为 null
    ts: float | None = None


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
    #: 控制台回显（截断）；命令失败时形如 "failed: ConsoleTimeout"
    result: str | None = None


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
    kind: str = "manual"          # manual | auto（Terraria 自己写的 .wld.bak）| legacy
    restorable: bool = True
    path: str = ""


class RestoreRequest(BaseModel):
    file: str | None = None


class BackupListResponse(BaseModel):
    backups: list[BackupEntry]


class UploadResponse(BaseModel):
    name: str
    file: str
    size: int


# ---------------------------------------------------------------- scheduler
class JobRun(BaseModel):
    """一次任务执行的结果。

    `detail` 是面向人的中文回退文案（不保证稳定）；`code` / `params` 才是
    给面板做程序判断的稳定字段（issue #18）。`code` 覆盖现有前缀：
    `ok` / `stalled` / `unavailable.busy` / `unavailable.console` / `error` /
    `skipped.no_players` / `skipped.players_online` / `skipped.console_unavailable` /
    `submitted` / `backup.done`。
    """

    at: float
    status: str            # succeeded | skipped | failed
    detail: str | None = None
    code: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    duration: float
    manual: bool


class SchedulerJob(BaseModel):
    name: str
    kind: str              # interval | daily
    description: str
    enabled: bool
    interval_seconds: float | None = None
    at: str | None = None
    next_run: float | None = None
    last_run: float | None = None
    last_status: str | None = None
    last_detail: str | None = None
    #: 与 last_detail 对应的结构化字段
    last_code: str | None = None
    last_params: dict[str, Any] = Field(default_factory=dict)
    run_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    history: list[JobRun] = Field(default_factory=list)


class SchedulerResponse(BaseModel):
    enabled: bool
    timezone: str
    jobs: list[SchedulerJob]


# ---------------------------------------------------------------- notifications
class DeliveryView(BaseModel):
    ts: float
    event: str
    title: str
    ok: bool
    status: int | None = None
    error: str | None = None


class NotificationQQStatus(BaseModel):
    """QQ 频道机器人的当前配置（`client_secret` 只回掩码）。"""

    app_id: str
    client_secret: str
    client_secret_set: bool
    channel_id: str
    sandbox: bool
    #: 高级字段：留空表示用官方默认域名
    api_base: str
    token_url: str


class NotificationStatus(BaseModel):
    """通知配置与最近投递。`enabled` 为 false 时看 `missing` 缺什么。"""

    enabled: bool
    #: auto | discord | slack | feishu | json | qq
    provider: str
    #: 实际生效的渠道（provider=auto 时按 URL 猜出来的结果，或 qq）
    format: str
    #: 只回主机名（webhook URL 本身就是凭据）
    url: str
    url_set: bool
    events: list[str] | str
    #: 配置不完整时缺哪些字段（如 `["url"]` / `["qq.app_id"]`）
    missing: list[str]
    #: env = 来自 NOTIFY_* 环境变量；file = 来自 control/notify.json
    source: str
    qq: NotificationQQStatus
    deliveries: list[DeliveryView]


class NotificationQQUpdate(BaseModel):
    """QQ 字段的更新；省略 / `null` = 保持不变，`""` = 清空。"""

    app_id: str | None = None
    #: 传掩码（`••••••`）表示"不改"
    client_secret: str | None = None
    channel_id: str | None = None
    sandbox: bool | None = None
    api_base: str | None = None
    token_url: str | None = None


class NotificationSettingsUpdate(BaseModel):
    """更新通知目标。

    **合并语义**：只改带来的字段——省略 / `null` 保持原值，`""` 清空，
    掩码（`••••••` 或 `https://host/…`）表示"不改"。
    """

    provider: str | None = None
    url: str | None = None
    events: str | None = None
    qq: NotificationQQUpdate | None = None


# ---------------------------------------------------------------- guard
class GuardAllowEntry(BaseModel):
    ip: str
    source: str          # static | learned
    expires_at: float | None = None


class GuardBanEntry(BaseModel):
    ip: str
    expires_at: float | None = None


class GuardCounters(BaseModel):
    """计数器的**所有**字段都是必填：守卫不在时后端填 0，而不是省略整个对象。

    否则客户端只能防御性地写 `counters?.bans_total ?? 0`（见 issue #8）。
    """

    bans_total: int
    commands_total: int
    learned_total: int
    degraded_console: int


class GuardState(BaseModel):
    """守卫进程的连接守卫状态（防扫描/白名单/封禁）。

    守卫未运行时**仍然返回完整结构**（`available: false` + 空数组 + 全 0 计数器），
    因此这里不设默认值：所有字段都是必填，客户端不需要兜底判断。
    """

    available: bool
    stale: bool
    age: float
    updated_at: float | None
    port: int | None
    allowlist_only: bool
    allow: list[GuardAllowEntry]
    banned: list[GuardBanEntry]
    counters: GuardCounters


class GuardIpRequest(BaseModel):
    ip: str


class GuardBanRequest(BaseModel):
    ip: str
    seconds: int | None = Field(default=None, ge=60, le=604800)


class GuardActionResult(BaseModel):
    ok: bool
    message: str
    command_id: str | None = None
