"""控制台心跳：用来发现"日志停更"（本轮真踩过的事故）。

关键是别误报：没人在线、面板没开的时候日志本来就可以安静很久，
所以心跳是"主动发一条命令看有没有回显"，而不是看日志时间戳。
"""

from __future__ import annotations

import threading
import time

import pytest

from app.core.settings import Settings
from app.services.runtime import _job_result_notifier, build_job_specs, build_runtime


def test_heartbeat_ok_when_console_responds(rt) -> None:
    assert rt.server.console_heartbeat() == "ok"


def test_heartbeat_reports_stall_when_log_stops(rt, fake_terraria) -> None:
    fake_terraria.swallow_output = True          # 命令能收到，但日志不写
    detail = rt.server.console_heartbeat(timeout=0.5)
    assert detail.startswith("stalled:"), detail
    assert "没有回显" in detail


def test_stall_alert_has_a_cooldown(rt, fake_terraria) -> None:
    fake_terraria.swallow_output = True
    assert rt.server.console_heartbeat(timeout=0.5).startswith("stalled:")
    second = rt.server.console_heartbeat(timeout=0.5)
    assert second.startswith("stalled (") and "冷却" in second


def test_unavailable_is_not_reported_as_a_stall(settings, tmp_path) -> None:
    """服务端在重启（FIFO 写不进去）时不能报"日志停更"。"""
    settings = Settings(
        worlds_dir=tmp_path / "worlds",
        config_file=tmp_path / "serverconfig.txt",
        control_dir=tmp_path / "missing-control",     # FIFO 不存在
        backup_dir=tmp_path / "backup",
        schedule_enabled=False,
    )
    runtime = build_runtime(settings)
    detail = runtime.server.console_heartbeat(timeout=0.3)
    assert detail.startswith("unavailable:"), detail


def test_restart_in_flight_is_not_reported_as_a_stall(rt) -> None:
    """重启/切世界期间日志本来就会安静，探活必须让路，否则每次重启都误报。"""
    release = threading.Event()
    rt.operations.submit("server.restart", lambda progress: release.wait(5))
    try:
        detail = rt.server.console_heartbeat(timeout=0.3)
    finally:
        release.set()
    assert detail.startswith("unavailable:"), detail
    assert "server.restart" in detail


def test_heartbeat_job_is_registered(rt) -> None:
    settings = Settings(
        schedule_console_check_seconds=30,
        schedule_save_minutes=0,
        schedule_backup_hours=0,
        schedule_restart_at="",
        schedule_enabled=False,
    )
    specs = {spec.name: spec for spec in build_job_specs(settings, rt.server, rt.world, rt.notifier)}
    assert specs["console"].interval_seconds == 30
    assert specs["console"].runner() == "ok"      # 绑定方法用 is 比较不成立，直接调用验证


def test_heartbeat_job_can_be_disabled(rt) -> None:
    settings = Settings(
        schedule_console_check_seconds=0,
        schedule_save_minutes=0,
        schedule_backup_hours=0,
        schedule_restart_at="",
        schedule_enabled=False,
    )
    assert build_job_specs(settings, rt.server, rt.world, rt.notifier) == []


def test_stall_triggers_a_notification(rt) -> None:
    sent: list[tuple[str, str]] = []

    class Recorder:
        def notify(self, event, title, *, level="info", detail=None):
            sent.append((event, level))

    hook = _job_result_notifier(Recorder())
    hook("console", "succeeded", "stalled: 控制台命令写入成功但 3 秒内没有回显")
    hook("console", "succeeded", "ok")
    hook("console", "succeeded", "stalled (已告警过，冷却中)")
    hook("console", "succeeded", "unavailable: 无法写入控制 FIFO")
    assert sent == [("console_stalled", "error")]


def test_log_age_is_exposed(rt) -> None:
    age = rt.reader.age()
    assert age is not None and age < 60
