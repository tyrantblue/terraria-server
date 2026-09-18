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
import re
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

#: `unavailable: <kind> 正在进行（<id>），跳过探活`
_BUSY_RE = re.compile(r"^unavailable:\s*(?P<kind>[\w.]+)\s+正在进行（(?P<id>[^）]+)）")
#: `skipped: 3 人在线`
_PLAYERS_RE = re.compile(r"^skipped:\s*(?P<online>\d+)\s*人在线")
#: `backup=20260917-170849 files=3 pruned=1`
_BACKUP_RE = re.compile(r"^backup=(?P<backup>\S+)\s+files=(?P<files>\d+)\s+pruned=(?P<pruned>\d+)")


def classify_detail(
    detail: str | None, *, status: str = "succeeded"
) -> tuple[str | None, dict[str, object]]:
    """把 runner 的中文自由文案提升成稳定的 `(code, params)`（issue #18）。

    面板不再需要 `detail.startsWith('stalled')` 这类字符串约定，也不再正则抠
    operation id。`detail` 原样保留，作为面向人的回退文案。
    """
    if not detail:
        return None, {}
    text = detail.strip()
    if not text:
        return None, {}

    if text.startswith("submitted"):
        _, _, operation_id = text.partition(":")
        return "submitted", {"operation_id": operation_id.strip()}
    if text.startswith("stalled"):
        return "stalled", {}
    if text.startswith("unavailable"):
        busy = _BUSY_RE.match(text)
        if busy:
            return "unavailable.busy", {
                "kind": busy.group("kind"),
                "operation_id": busy.group("id"),
            }
        return "unavailable.console", {}
    if text.startswith("error"):
        return "error", {}
    if text.startswith("skipped"):
        players = _PLAYERS_RE.match(text)
        if players:
            return "skipped.players_online", {"online": int(players.group("online"))}
        if "控制台不可用" in text:
            return "skipped.console_unavailable", {}
        if "无人在线" in text:
            return "skipped.no_players", {"online": 0}
        return "skipped.other", {}
    backup = _BACKUP_RE.match(text)
    if backup:
        return "backup.done", {
            "backup": backup.group("backup"),
            "files": int(backup.group("files")),
            "pruned": int(backup.group("pruned")),
        }
    if text == "ok" or text.startswith("ok"):
        return "ok", {}
    if text == "saved":
        return "ok", {}

    fallback = {"succeeded": "ok", "skipped": "skipped.other"}.get(status, "error")
    return fallback, {}


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
    last_code: str | None = None
    last_params: dict[str, object] = field(default_factory=dict)
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
                        "last_code": state.last_code,
                        "last_params": dict(state.last_params),
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
        # 把「前缀 + 中文后缀」升级为稳定的 code/params（issue #18），
        # 同时保持 detail / last_status 的现有语义不变。
        code, params = classify_detail(detail, status=status)
        entry: dict[str, object] = {
            "at": finished,
            "status": status,
            "detail": detail,
            "code": code,
            "params": params,
            "duration": round(finished - started, 3),
            "manual": manual,
        }
        # issue #10：restart 类任务提交的是后台 operation，面板要拿**顶层**
        # `submitted` 去轮询 /api/v1/operations/{id}，而不是立刻显示「已完成」。
        if code == "submitted" and params.get("operation_id"):
            entry["submitted"] = params["operation_id"]

        with self._lock:
            state = self.states[spec.name]
            state.last_run = finished
            state.last_status = status
            state.last_detail = detail
            state.last_code = code
            state.last_params = dict(params)
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
