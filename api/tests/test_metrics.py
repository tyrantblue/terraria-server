"""资源/在线人数采样（issue #2 第 2 部分）与 `GET /api/v1/metrics`。

cgroup 用假目录测（真实容器里是 cgroup v2：cpu.stat / cpu.max / memory.current），
这样 CPU 百分比、配额折算、缺失文件降级都能确定性地覆盖。
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.metrics import (
    MetricsSampler,
    read_cpu_cores,
    read_cpu_usage_usec,
    read_memory,
)
from app.services.runtime import build_runtime


def _fake_cgroup(
    tmp_path: Path,
    *,
    usage_usec: int = 1_000_000,
    cpu_max: str = "max 100000",
    cpus: str = "0-3",
    memory_current: int = 64 * 1024 * 1024,
    memory_max: str = "max",
) -> Path:
    root = tmp_path / "cgroup"
    root.mkdir(exist_ok=True)
    (root / "cpu.stat").write_text(f"usage_usec {usage_usec}\nuser_usec 1\n", encoding="utf-8")
    (root / "cpu.max").write_text(f"{cpu_max}\n", encoding="utf-8")
    (root / "cpuset.cpus.effective").write_text(f"{cpus}\n", encoding="utf-8")
    (root / "memory.current").write_text(f"{memory_current}\n", encoding="utf-8")
    (root / "memory.max").write_text(f"{memory_max}\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------- cgroup 读取
def test_read_cpu_usage_from_v2_stat(tmp_path: Path) -> None:
    root = _fake_cgroup(tmp_path, usage_usec=123456)
    assert read_cpu_usage_usec(root) == 123456


def test_read_cpu_cores_from_quota(tmp_path: Path) -> None:
    assert read_cpu_cores(_fake_cgroup(tmp_path, cpu_max="200000 100000")) == 2.0


def test_read_cpu_cores_falls_back_to_cpuset(tmp_path: Path) -> None:
    """cpu.max 是 `max`（没配额）时按 cpuset 的核数折算。"""
    assert read_cpu_cores(_fake_cgroup(tmp_path, cpu_max="max 100000", cpus="0-1")) == 2.0
    assert read_cpu_cores(_fake_cgroup(tmp_path, cpu_max="max 100000", cpus="0,2,3")) == 3.0


def test_read_memory_limit_and_max(tmp_path: Path) -> None:
    current, limit = read_memory(_fake_cgroup(tmp_path, memory_current=1000, memory_max="max"))
    assert (current, limit) == (1000, None)
    current, limit = read_memory(
        _fake_cgroup(tmp_path, memory_current=1000, memory_max=str(2 * 1024 * 1024))
    )
    assert (current, limit) == (1000, 2 * 1024 * 1024)


def test_missing_cgroup_files_degrade_to_none(tmp_path: Path) -> None:
    empty = tmp_path / "nothing"
    assert read_cpu_usage_usec(empty) is None
    assert read_cpu_cores(empty) is None
    assert read_memory(empty) == (None, None)


# ---------------------------------------------------------------- 采样
def test_cpu_percent_is_relative_to_available_cores(tmp_path: Path) -> None:
    root = _fake_cgroup(tmp_path, usage_usec=1_000_000, cpu_max="max 100000", cpus="0-3")
    sampler = MetricsSampler(tmp_path, players_provider=lambda: 3, cgroup_root=root)
    sampler._collect(now=1000.0)  # 第一个点只能校准，算不出速率
    root.joinpath("cpu.stat").write_text("usage_usec 3000000\n", encoding="utf-8")
    point = sampler._collect(now=1010.0)

    # 10 秒里用掉 2 秒 CPU = 单核 20%；4 个核可分 → 5%
    assert point.cpu_percent == pytest.approx(5.0)
    assert point.cpu_cores == 4.0
    assert point.memory_bytes == 64 * 1024 * 1024
    assert point.players_online == 3


def test_sampler_records_points_and_filters_window(tmp_path: Path) -> None:
    sampler = MetricsSampler(tmp_path, players_provider=lambda: 1, cgroup_root=tmp_path)
    sampler.sample(now=time.time())
    sampler.sample(now=time.time() - 3600)  # 一小时前的点
    assert len(sampler.snapshot()) == 2
    assert len(sampler.snapshot(minutes=5)) == 1
    assert sampler.latest() is not None


def test_sampler_retention_is_bounded(tmp_path: Path) -> None:
    sampler = MetricsSampler(tmp_path, retention=3, cgroup_root=tmp_path)
    for _ in range(10):
        sampler.sample()
    assert len(sampler.snapshot()) == 3


def test_players_provider_failure_is_not_fatal(tmp_path: Path) -> None:
    def boom() -> int:
        raise RuntimeError("console down")

    sampler = MetricsSampler(tmp_path, players_provider=boom, cgroup_root=tmp_path)
    assert sampler.sample().players_online is None


def test_disk_is_sampled(tmp_path: Path) -> None:
    sampler = MetricsSampler(tmp_path, cgroup_root=tmp_path)
    point = sampler.sample()
    assert point.disk_total_bytes and point.disk_total_bytes > 0
    assert point.disk_free_bytes is not None


# ---------------------------------------------------------------- 接口
def test_metrics_endpoint(client, rt) -> None:
    rt.metrics.sample()
    body = client.get("/api/v1/metrics").json()
    assert body["interval_seconds"] == rt.metrics.interval
    assert body["retention_points"] == rt.metrics.retention
    assert body["window_minutes"] == 60.0
    assert body["points"], "至少应有一个刚采的点"
    assert body["latest"]["ts"] == body["points"][-1]["ts"]


def test_metrics_schema_shape(client, rt) -> None:
    rt.metrics.sample()
    point = client.get("/api/v1/metrics").json()["points"][0]
    assert set(point) == {
        "ts", "cpu_percent", "cpu_cores", "memory_bytes", "memory_limit_bytes",
        "disk_free_bytes", "disk_total_bytes", "players_online",
    }


def test_metrics_window_is_validated(client) -> None:
    assert client.get("/api/v1/metrics", params={"minutes": 0}).status_code == 422
    assert client.get("/api/v1/metrics", params={"minutes": 5000}).status_code == 422


def test_lifespan_starts_and_stops_the_sampler(settings, fake_terraria) -> None:
    runtime = build_runtime(dataclasses.replace(settings, metrics_interval_seconds=1.0))
    with TestClient(create_app(runtime)):
        time.sleep(0.2)
        assert runtime.metrics.is_alive()
        assert runtime.metrics.snapshot()

    deadline = time.time() + 3
    while runtime.metrics.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    assert not runtime.metrics.is_alive()


def test_thread_can_be_joined(rt) -> None:
    """回归：Thread 子类里不能把停止事件叫 `_stop`——它会把 `Thread._stop()`
    覆盖掉，之后 `is_alive()` / `join()` 会抛 TypeError（'Event' object is not callable）。"""
    sampler = rt.metrics
    sampler.start()
    time.sleep(0.05)
    sampler.stop()
    sampler.join(timeout=3)
    assert not sampler.is_alive()

    watcher = rt.events
    watcher.start()
    time.sleep(0.05)
    watcher.stop()
    watcher.join(timeout=3)
    assert not watcher.is_alive()
