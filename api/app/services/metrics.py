"""资源与在线人数采样（issue #2 第 2 部分）。

面板要画曲线，后端就要有历史点。这里**不引入任何额外组件**：
* CPU / 内存直接读容器自己的 cgroup（v2 与 v1 都支持，读不到就是 `None`）；
* 磁盘余量用 `statvfs`；
* 在线人数复用已有的控制台状态缓存（不额外发明一套）。

采样放在后台线程里，点存在内存 `deque` 里（默认 24 小时 / 1 分钟粒度），
API 重启即清空——够画曲线，也不用引入时序数据库。

CPU 百分比：cgroup 给的是**累计 CPU 时间**，所以要用两次采样的差值除以间隔。
配额按 `cpu.max`（v2）或 `cfs_quota/cfs_period`（v1）折算；没有配额时按
`cpuset.cpus.effective` 的核数折算，这样「100%」表示把分配的核全部吃满。
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

#: 容器里 cgroup 的挂载点（测试可覆盖）
CGROUP_ROOT = Path(os.environ.get("TERRARIA_CGROUP_ROOT", "/sys/fs/cgroup"))


@dataclass(frozen=True)
class MetricSample:
    ts: float
    cpu_percent: float | None
    cpu_cores: float | None
    memory_bytes: int | None
    memory_limit_bytes: int | None
    disk_free_bytes: int | None
    disk_total_bytes: int | None
    players_online: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "ts": self.ts,
            "cpu_percent": self.cpu_percent,
            "cpu_cores": self.cpu_cores,
            "memory_bytes": self.memory_bytes,
            "memory_limit_bytes": self.memory_limit_bytes,
            "disk_free_bytes": self.disk_free_bytes,
            "disk_total_bytes": self.disk_total_bytes,
            "players_online": self.players_online,
        }


# ---------------------------------------------------------------- cgroup
def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def read_cpu_usage_usec(root: Path = CGROUP_ROOT) -> int | None:
    """累计 CPU 时间（微秒）。v2 在 cpu.stat，v1 在 cpuacct.usage（纳秒）。"""
    stat = _read_text(root / "cpu.stat")
    if stat:
        for line in stat.splitlines():
            key, _, value = line.partition(" ")
            if key == "usage_usec":
                try:
                    return int(value.strip())
                except ValueError:
                    return None
    legacy = _read_text(root / "cpuacct" / "cpuacct.usage")
    if legacy:
        try:
            return int(legacy) // 1000  # 纳秒 → 微秒
        except ValueError:
            return None
    return None


def read_cpu_cores(root: Path = CGROUP_ROOT) -> float | None:
    """容器可用的核心数（用于把 CPU 时间折算成百分比）。"""
    quota = _read_text(root / "cpu.max")
    if quota:
        parts = quota.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                return max(0.01, int(parts[0]) / int(parts[1]))
            except (ValueError, ZeroDivisionError):
                pass
    # v1
    cfs_quota = _read_text(root / "cpu" / "cpu.cfs_quota_us")
    cfs_period = _read_text(root / "cpu" / "cpu.cfs_period_us")
    if cfs_quota and cfs_period:
        try:
            quota_us = int(cfs_quota)
            period_us = int(cfs_period)
            if quota_us > 0 and period_us > 0:
                return max(0.01, quota_us / period_us)
        except ValueError:
            pass
    # 没有配额：数 cpuset 里列了几个核
    cpuset = _read_text(root / "cpuset.cpus.effective") or _read_text(
        root / "cpuset" / "cpuset.cpus"
    )
    count = _count_cpus(cpuset)
    return float(count) if count else None


def _count_cpus(spec: str | None) -> int:
    """数 `0-1,3` 这类 cpuset 列表里的核数。"""
    if not spec:
        return 0
    total = 0
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            low, _, high = part.partition("-")
            try:
                total += max(0, int(high) - int(low) + 1)
            except ValueError:
                continue
        else:
            try:
                int(part)
            except ValueError:
                continue
            total += 1
    return total


def read_memory(root: Path = CGROUP_ROOT) -> tuple[int | None, int | None]:
    """(当前用量, 上限)。上限为 `max`（没限制）时返回 None。"""
    current = _read_text(root / "memory.current")
    limit = _read_text(root / "memory.max")
    if current is None:
        current = _read_text(root / "memory" / "memory.usage_in_bytes")
        limit = _read_text(root / "memory" / "memory.limit_in_bytes")

    usage: int | None = None
    if current:
        try:
            usage = int(current)
        except ValueError:
            usage = None

    ceiling: int | None = None
    if limit and limit != "max":
        try:
            ceiling = int(limit)
        except ValueError:
            ceiling = None
        else:
            # v1 在「没有限制」时会写一个天文数字
            if ceiling > 1 << 50:
                ceiling = None
    return usage, ceiling


# ---------------------------------------------------------------- 采样器
class MetricsSampler(threading.Thread):
    def __init__(
        self,
        worlds_dir: Path,
        *,
        players_provider: Callable[[], int | None] | None = None,
        interval: float = 60.0,
        retention: int = 1440,
        cgroup_root: Path = CGROUP_ROOT,
    ) -> None:
        super().__init__(name="metrics", daemon=True)
        self.worlds_dir = worlds_dir
        self.interval = max(1.0, float(interval))
        self.retention = max(1, int(retention))
        self.cgroup_root = cgroup_root
        self._players_provider = players_provider
        self._points: deque[MetricSample] = deque(maxlen=self.retention)
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        #: 上一次的 (采样时刻, 累计 CPU 微秒)，用于算速率
        self._last_cpu: tuple[float, int] | None = None

    # -- 采集 ---------------------------------------------------------
    def _players(self) -> int | None:
        if self._players_provider is None:
            return None
        try:
            return self._players_provider()
        except Exception as exc:  # noqa: BLE001 - 控制台不可用时曲线少一个点而已
            logger.debug("metrics: 在线人数读取失败：%s", exc)
            return None

    def _collect(self, now: float | None = None) -> MetricSample:
        moment = time.time() if now is None else now

        usage = read_cpu_usage_usec(self.cgroup_root)
        cores = read_cpu_cores(self.cgroup_root)
        cpu_percent: float | None = None
        if usage is not None:
            previous = self._last_cpu
            if previous is not None:
                elapsed = moment - previous[0]
                delta = usage - previous[1]
                if elapsed > 0 and delta >= 0:
                    divisor = cores or 1.0
                    cpu_percent = round(delta / 1_000_000 / elapsed / divisor * 100, 2)
            self._last_cpu = (moment, usage)

        memory_bytes, memory_limit = read_memory(self.cgroup_root)

        disk_free = disk_total = None
        try:
            disk = shutil.disk_usage(str(self.worlds_dir))
            disk_free, disk_total = disk.free, disk.total
        except OSError:
            pass

        return MetricSample(
            ts=moment,
            cpu_percent=cpu_percent,
            cpu_cores=cores,
            memory_bytes=memory_bytes,
            memory_limit_bytes=memory_limit,
            disk_free_bytes=disk_free,
            disk_total_bytes=disk_total,
            players_online=self._players(),
        )

    def sample(self, now: float | None = None) -> MetricSample:
        """采一个点并记入内存（测试与「立刻要一个点」时直接调用）。"""
        point = self._collect(now)
        with self._lock:
            self._points.append(point)
        return point

    # -- 查询 ---------------------------------------------------------
    def snapshot(self, *, minutes: float | None = None) -> list[MetricSample]:
        with self._lock:
            points = list(self._points)
        if minutes is None:
            return points
        floor = time.time() - minutes * 60
        return [point for point in points if point.ts >= floor]

    def latest(self) -> MetricSample | None:
        with self._lock:
            return self._points[-1] if self._points else None

    # -- 线程 ---------------------------------------------------------
    def run(self) -> None:  # pragma: no cover - 线程体
        while not self._stopped.is_set():
            try:
                self.sample()
            except Exception as exc:  # noqa: BLE001 - 采样失败不能让线程死掉
                logger.warning("metrics: 采样失败：%s", exc)
            self._stopped.wait(self.interval)

    def stop(self) -> None:
        self._stopped.set()
