"""定时任务：定时保存、定时备份 + 清理、定时重启。

roadmap 的第一优先级。设计取舍：

* 用**独立守护线程**而不是 asyncio：现有 service 全是同步 + FIFO 阻塞式的，
  放进事件循环反而要到处 `to_thread`。
* 时区显式配置（`SCHEDULE_TZ`，默认 Asia/Shanghai）。容器是 UTC，
  不指定的话「凌晨 5 点重启」会变成北京时间中午 12 点。
* 任务本身只做「该不该跑」的判断，真正的动作通过回调注入（runner），
  这样 scheduler 不依赖 console/world，便于测试。
* 每次执行的结果（成功/失败/跳过 + 原因）都记下来，面板可以直接看。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

INTERVAL = "interval"
DAILY = "daily"

#: runner 返回值：None 或字符串都会记进 last_detail
Runner = "Callable[[], str | None]"


@dataclass
class JobSpec:
    name: str
    kind: str
    runner: object  # Callable[[], str | None]
    description: str = ""
    interval_seconds: float | None = None
    at: str | None = None  # "05:00"
    enabled: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "at": self.at,
        }


@dataclass
class JobState:
    next_run: float | None = None
    last_run: float | None = None
    last_status: str | None = None
    last_detail: str | None = None
    run_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    history: list[dict[str, object]] = field(default_factory=list)


class Scheduler:
    def __init__(
        self,
        specs: list[JobSpec],
        *,
        timezone: str = "Asia/Shanghai",
        tick: float = 5.0,
        history: int = 20,
        on_result: object | None = None,
    ) -> None:
        try:
            self.tz = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("未知时区 %r，退回 UTC", timezone)
            self.tz = ZoneInfo("UTC")
        self.specs = {spec.name: spec for spec in specs}
        self.states = {spec.name: JobState() for spec in specs}
        self._tick = tick
        self._history = history
        #: Callable[[str, str, str | None], None]：任务跑完后回调（用于通知）
        self._on_result = on_result
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._now = time.time()
        for name in self.specs:
            self._schedule_next(name)

    # -- 生命周期 -----------------------------------------------------
    def start(self) -> None:
        if not any(spec.enabled for spec in self.specs.values()):
            logger.info("scheduler: 没有启用的任务")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()
        logger.info(
            "scheduler: 已启动 %s（时区 %s）",
            ", ".join(spec.name for spec in self.specs.values() if spec.enabled),
            self.tz.key,
        )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)

    # -- 查询 ---------------------------------------------------------
    def list_jobs(self) -> list[dict[str, object]]:
        with self._lock:
            result = []
            for name, spec in self.specs.items():
                state = self.states[name]
                result.append(
                    {
                        **spec.as_dict(),
                        "next_run": state.next_run,
                        "last_run": state.last_run,
                        "last_status": state.last_status,
                        "last_detail": state.last_detail,
                        "run_count": state.run_count,
                        "skipped_count": state.skipped_count,
                        "failed_count": state.failed_count,
                        "history": list(state.history),
                    }
                )
            return result

    # -- 执行 ---------------------------------------------------------
    def run_now(self, name: str) -> dict[str, object]:
        """手动触发一次（面板的「立即执行」）。同步执行，返回本次结果。"""
        spec = self.specs.get(name)
        if spec is None:
            raise KeyError(name)
        return self._execute(spec, manual=True)

    def _execute(self, spec: JobSpec, *, manual: bool = False) -> dict[str, object]:
        started = time.time()
        status, detail = "succeeded", None
        try:
            result = spec.runner()  # type: ignore[operator]
            if isinstance(result, str):
                detail = result
                if result.startswith("skipped"):
                    status = "skipped"
        except Exception as exc:  # noqa: BLE001 - 任务失败不能拖垮调度线程
            status, detail = "failed", str(exc)
            logger.warning("scheduler: 任务 %s 失败: %s", spec.name, exc)

        finished = time.time()
        entry = {
            "at": finished,
            "status": status,
            "detail": detail,
            "duration": round(finished - started, 3),
            "manual": manual,
        }
        with self._lock:
            state = self.states[spec.name]
            state.last_run = finished
            state.last_status = status
            state.last_detail = detail
            state.run_count += 1
            if status == "skipped":
                state.skipped_count += 1
            elif status == "failed":
                state.failed_count += 1
            state.history.insert(0, entry)
            del state.history[self._history :]
            if not manual:
                self._schedule_next(spec.name, after=finished)
            else:
                # 手动执行不改变既有节奏，但把下次时间往后顺延，避免刚跑完又自动跑
                self._schedule_next(spec.name, after=finished)
        logger.info("scheduler: %s -> %s %s", spec.name, status, detail or "")
        if self._on_result is not None and not manual:
            try:
                self._on_result(spec.name, status, detail)  # type: ignore[operator]
            except Exception as exc:  # noqa: BLE001
                logger.warning("scheduler: on_result 回调异常: %s", exc)
        return entry

    # -- 内部 ---------------------------------------------------------
    def _schedule_next(self, name: str, *, after: float | None = None) -> None:
        spec = self.specs[name]
        state = self.states[name]
        if not spec.enabled:
            state.next_run = None
            return
        base = after or time.time()
        if spec.kind == INTERVAL and spec.interval_seconds:
            state.next_run = base + spec.interval_seconds
            return
        if spec.kind == DAILY and spec.at:
            state.next_run = self._next_daily(spec.at, base)
            return
        state.next_run = None

    def _next_daily(self, at: str, base: float) -> float:
        hour, _, minute = at.partition(":")
        now_local = datetime.fromtimestamp(base, self.tz)
        target = now_local.replace(
            hour=int(hour), minute=int(minute or 0), second=0, microsecond=0
        )
        if target <= now_local:
            target += timedelta(days=1)
        return target.timestamp()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._now = time.time()
                due = [
                    spec
                    for name, spec in self.specs.items()
                    if spec.enabled
                    and self.states[name].next_run is not None
                    and self.states[name].next_run <= self._now
                ]
                for spec in due:
                    self._execute(spec)
            except Exception as exc:  # noqa: BLE001
                logger.warning("scheduler 循环异常: %s", exc)
            self._stop.wait(self._tick)
