"""长耗时操作的登记与执行。

旧实现把「切世界」塞进一个同步 HTTP 请求里 sleep + 轮询最多 30 秒，前端只能干等，
超时返回 504 但世界其实可能已经切完了。现在改成：

    POST /api/v1/worlds/{file}/activate  ->  202 {"operation_id": "..."}
    GET  /api/v1/operations/{id}         ->  {"state": "running", "progress": 65, ...}

操作在后台线程里执行，进度通过回调上报。状态只存在内存里（API 重启即丢），
这对一个单进程的管理面板够用；需要长期历史的话再落盘。
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

#: 这些操作会动服务端进程/世界文件，必须串行，不能两个一起跑
EXCLUSIVE_KINDS = {"server.restart", "world.activate", "config.apply"}

ProgressFn = Callable[[int, str], None]
JobFn = Callable[[ProgressFn], dict[str, Any] | None]


@dataclass
class Operation:
    id: str
    kind: str
    state: str = PENDING
    progress: int = 0
    message: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "progress": self.progress,
            "message": self.message,
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
            raise NotFound(f"operation not found: {operation_id}")
        return operation

    def list(self) -> list[Operation]:
        with self._lock:
            return [self._ops[oid] for oid in reversed(self._order) if oid in self._ops]

    def busy(self) -> Operation | None:
        """当前是否有会动服务端的操作在跑。"""
        with self._lock:
            for oid in self._order:
                operation = self._ops.get(oid)
                if (
                    operation
                    and operation.kind in EXCLUSIVE_KINDS
                    and operation.state in (PENDING, RUNNING)
                ):
                    return operation
        return None

    # -- 提交 ---------------------------------------------------------
    def submit(self, kind: str, job: JobFn) -> Operation:
        if kind in EXCLUSIVE_KINDS:
            running = self.busy()
            if running is not None:
                raise Conflict(
                    f"另一个会重启服务端的操作正在进行：{running.kind} ({running.id})",
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

        def progress(value: int, message: str) -> None:
            operation.progress = max(0, min(100, int(value)))
            operation.message = message

        try:
            result = job(progress)
        except Exception as exc:  # noqa: BLE001 - 任何异常都要落到操作状态里
            operation.state = FAILED
            operation.error = str(exc)
            operation.message = "failed"
        else:
            operation.state = SUCCEEDED
            operation.result = result or {}
            operation.progress = 100
            operation.message = "succeeded"
        finally:
            operation.finished_at = time.time()
