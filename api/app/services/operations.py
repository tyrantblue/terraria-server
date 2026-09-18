"""长耗时操作的登记与执行。

旧实现把「切世界」塞进一个同步 HTTP 请求里 sleep + 轮询最多 30 秒，前端只能干等，
超时返回 504 但世界其实可能已经切完了。现在改成：

    POST /api/v1/worlds/{file}/activate  ->  202 {"operation_id": "..."}
    GET  /api/v1/operations/{id}         ->  {"state": "running", "progress": 65, ...}

操作在后台线程里执行，进度通过回调上报。状态只存在内存里（API 重启即丢），
这对一个单进程的管理面板够用；需要长期历史的话再落盘。

保留策略（issue #15 要求写进文档）：内存里最多 `max_history` 条（默认 50），
新的在前，超出后按提交顺序淘汰最旧的；API 重启即清空。因此
`GET /api/v1/operations/{id}` 的 404 使用专用错误码 `operation_not_found`，
与「路由不存在」的 `not_found` 区分开——面板据此提示「结果未知，不要自动重试」。

结构化文案（issue #18）：`message` 是面向人的中文回退文案，`message_code` /
`message_params` 才是给面板做程序判断与本地化的稳定字段。
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.errors import Conflict, NotFound

PENDING = "pending"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"

TERMINAL_STATES = frozenset({SUCCEEDED, FAILED})

#: 这些操作会动服务端进程/世界文件，必须串行，不能两个一起跑。
#: `world.restore` 会「停服 → 覆盖世界文件 → 再启动」，与 `server.restart`
#: 属于同一类（issue #12）。`config.apply` 从未被真正提交过（配置生效走的是
#: `server.restart`），所以从集合里删掉，别再让它出现在文档里。
EXCLUSIVE_KINDS = frozenset({"server.restart", "world.activate", "world.restore"})

#: 非互斥操作的单向约束：提交 key 时，若 value 里的某个 kind 正在跑则 409。
#: 备份会读世界文件，而恢复正在覆盖它——可能备份到半成品（issue #12 第 2 点）。
CONFLICTS_WITH: dict[str, frozenset[str]] = {
    "world.backup": frozenset({"world.restore"}),
}

#: 面板不要硬编码 kind→文案的映射表：后端自己发布自己的词汇表（issue #18 第 4 点）。
KIND_LABELS: dict[str, str] = {
    "server.restart": "Restart server",
    "world.activate": "Switch world",
    "world.restore": "Restore backup",
    "world.backup": "Back up worlds",
}


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def operation_kinds() -> list[dict[str, str]]:
    """`GET /api/meta` 用：所有已知 kind 及人读标签。"""
    return [{"kind": kind, "label": label} for kind, label in sorted(KIND_LABELS.items())]


ProgressFn = Callable[..., None]
JobFn = Callable[[ProgressFn], dict[str, Any] | None]


@dataclass
class Operation:
    id: str
    kind: str
    state: str = PENDING
    progress: int = 0
    message: str = ""
    #: 稳定的机器可读文案标识（如 `restart.stopping_server`）；永不缺失
    message_code: str = PENDING
    #: 与 message_code 配套的参数（如 `{"file": "gogogo.wld"}`）
    message_params: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "kind_label": kind_label(self.kind),
            "state": self.state,
            "progress": self.progress,
            "message": self.message,
            "message_code": self.message_code,
            "message_params": self.message_params,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }


class OperationRegistry:
    def __init__(self, max_history: int = 50, workers: int = 2) -> None:
        self._ops: dict[str, Operation] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._max_history = max_history
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="op")

    # -- 查询 ---------------------------------------------------------
    def get(self, operation_id: str) -> Operation:
        with self._lock:
            operation = self._ops.get(operation_id)
        if operation is None:
            # 专用错误码（issue #15）：与「路由不存在」的 not_found 区分开，
            # 面板不必再靠 message 正则判断「API 重启导致 operation 丢失」。
            raise NotFound(
                f"operation not found: {operation_id}",
                code="operation_not_found",
                details={"operation_id": operation_id},
            )
        return operation

    def list(self, *, state: str | None = None) -> list[Operation]:
        """新的在前。`state` 非空时只返回该状态（面板轮询用）。"""
        with self._lock:
            operations = [
                self._ops[oid] for oid in reversed(self._order) if oid in self._ops
            ]
        if state is not None:
            operations = [operation for operation in operations if operation.state == state]
        return operations

    def in_flight(self) -> int:
        """还有多少个操作没到终态（Dashboard 用它替代拉全量）。"""
        with self._lock:
            return sum(
                1
                for operation in self._ops.values()
                if operation.state not in TERMINAL_STATES
            )

    def running_of_kinds(self, kinds: frozenset[str] | set[str]) -> Operation | None:
        """返回 `kinds` 里第一个还在跑（pending/running）的操作。"""
        with self._lock:
            for oid in self._order:
                operation = self._ops.get(oid)
                if (
                    operation
                    and operation.kind in kinds
                    and operation.state in (PENDING, RUNNING)
                ):
                    return operation
        return None

    def busy(self) -> Operation | None:
        """当前是否有会动服务端的操作在跑。"""
        return self.running_of_kinds(EXCLUSIVE_KINDS)

    # -- 提交 ---------------------------------------------------------
    def submit(self, kind: str, job: JobFn) -> Operation:
        # 互斥：会动服务端的操作之间两两互斥；备份另受「恢复进行中」的单向约束。
        blockers = EXCLUSIVE_KINDS if kind in EXCLUSIVE_KINDS else CONFLICTS_WITH.get(
            kind, frozenset()
        )
        if blockers:
            running = self.running_of_kinds(blockers)
            if running is not None:
                raise Conflict(
                    f"另一个会动服务端的操作正在进行：{running.kind} ({running.id})",
                    details={"operation_id": running.id, "kind": running.kind},
                )

        operation = Operation(id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._ops[operation.id] = operation
            self._order.append(operation.id)
            while len(self._order) > self._max_history:
                stale = self._order.pop(0)
                self._ops.pop(stale, None)

        self._pool.submit(self._run, operation, job)
        return operation

    # -- 执行 ---------------------------------------------------------
    def _run(self, operation: Operation, job: JobFn) -> None:
        operation.state = RUNNING
        operation.started_at = time.time()
        operation.message = "running"
        operation.message_code = RUNNING

        def progress(
            value: int,
            message: str,
            code: str | None = None,
            params: dict[str, Any] | None = None,
        ) -> None:
            operation.progress = max(0, min(100, int(value)))
            operation.message = message
            if code:
                operation.message_code = code
            if params is not None:
                operation.message_params = dict(params)

        try:
            result = job(progress)
        except Exception as exc:  # noqa: BLE001 - 任何异常都要落到操作状态里
            operation.state = FAILED
            operation.error = str(exc)
            operation.message = "failed"
            operation.message_code = FAILED
        else:
            operation.state = SUCCEEDED
            operation.result = result or {}
            operation.progress = 100
            operation.message = "succeeded"
            operation.message_code = SUCCEEDED
        finally:
            operation.finished_at = time.time()
