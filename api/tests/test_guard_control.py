"""守卫侧的命令通道与状态发布（文件 IPC 的那一半）。

API 侧在 test_guard_api.py 里用假守卫测；这里反过来，直接驱动真守卫的
GuardControl，确认命令能被执行、结果能写回、状态快照格式正确。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

import pytest

GUARD_SCRIPT = Path(__file__).resolve().parents[2] / "guard" / "terraria-watchd.py"
pytestmark = pytest.mark.skipif(not GUARD_SCRIPT.exists(), reason="guard/ 不在测试范围内")


@pytest.fixture(scope="module")
def daemon():
    spec = importlib.util.spec_from_file_location("terraria_watchd", GUARD_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def control(daemon, tmp_path):
    args = argparse.Namespace(
        ban=True, recover=False, no_learn=True, dry_run=False, backfill=False,
        once=True, verbose=False,
    )
    watchdog = daemon.Watchdog(args)
    watchdog.fw.has_ipset = True
    calls: list[list[str]] = []
    watchdog.fw._run = lambda argv: calls.append(argv) or True
    watchdog.fw._capture = lambda argv: (True, "")

    allow_file = tmp_path / "allow.txt"
    allow_file.write_text("# 手工白名单\n172.18.0.0/16\n1.1.1.1\n", encoding="utf-8")
    learned_file = tmp_path / "learned_allow.txt"
    learned_file.write_text("# 学习型\n2.2.2.2 0\n", encoding="utf-8")

    guard = daemon.GuardControl(
        watchdog.fw, watchdog,
        allow_file=allow_file, learned_file=learned_file,
        state_file=tmp_path / "guard-state.json",
        command_file=tmp_path / "guard-commands.jsonl",
    )
    return guard, calls, allow_file, tmp_path


def test_ban_command(control) -> None:
    guard, calls, _allow, _tmp = control
    ok, message = guard._execute("ban", {"ip": "9.9.9.9", "seconds": 120})
    assert ok and "120" in message
    assert ["ipset", "add", "tg_ban", "9.9.9.9", "timeout", "120", "-exist"] in calls


def test_unban_also_clears_strikes(control) -> None:
    guard, calls, _allow, _tmp = control
    guard.watchdog.strikes["9.9.9.9"].append(1.0)
    ok, _ = guard._execute("unban", {"ip": "9.9.9.9"})
    assert ok and "9.9.9.9" not in guard.watchdog.strikes
    assert ["ipset", "del", "tg_ban", "9.9.9.9"] in calls


def test_allow_writes_file_and_applies(control) -> None:
    guard, calls, allow_file, _tmp = control
    ok, message = guard._execute("allow", {"ip": "5.6.7.8"})
    assert ok and "allow.txt" in message
    assert "5.6.7.8" in allow_file.read_text(encoding="utf-8").split()
    assert ["ipset", "add", "tg_allow", "5.6.7.8", "-exist"] in calls


def test_disallow_removes_from_file_and_set(control) -> None:
    guard, calls, allow_file, _tmp = control
    ok, _ = guard._execute("disallow", {"ip": "1.1.1.1"})
    assert ok
    assert "1.1.1.1" not in allow_file.read_text(encoding="utf-8").split()
    assert ["ipset", "del", "tg_allow", "1.1.1.1"] in calls


def test_reload_syncs_from_files(control) -> None:
    guard, calls, _allow, _tmp = control
    guard.fw.set_members = lambda name: [{"ip": "172.18.0.0/16", "expires_at": None},
                                        {"ip": "3.3.3.3", "expires_at": None}]
    ok, message = guard._execute("reload", {})
    assert ok and "白名单" in message
    assert any(call[:3] == ["ipset", "add", "tg_allow"] and call[3] == "1.1.1.1" for call in calls)
    assert ["ipset", "del", "tg_allow", "3.3.3.3"] in calls


def test_rejects_unknown_action_and_bad_ip(control) -> None:
    guard, calls, _allow, _tmp = control
    assert guard._execute("rm -rf", {"ip": "1.2.3.4"})[0] is False
    assert guard._execute("ban", {"ip": "1.2.3.4; reboot"})[0] is False
    assert guard._execute("ban", {})[0] is False
    assert calls == []


def test_poll_commands_executes_and_records(control) -> None:
    guard, _calls, _allow, tmp = control
    command_file = tmp / "guard-commands.jsonl"
    command_file.write_text(
        json.dumps({"id": "abc123", "action": "ban", "args": {"ip": "9.9.9.9"}, "ts": time.time()}) + "\n",
        encoding="utf-8",
    )
    guard.poll_commands()
    assert guard._results["abc123"]["ok"] is True

    state = json.loads((tmp / "guard-state.json").read_text(encoding="utf-8"))
    assert state["results"]["abc123"]["ok"] is True
    assert state["counters"]["commands_total"] == 1
    assert state["port"] == daemon_port_default(guard)


def test_expired_command_is_rejected(control) -> None:
    guard, _calls, _allow, tmp = control
    (tmp / "guard-commands.jsonl").write_text(
        json.dumps({"id": "old1", "action": "ban", "args": {"ip": "9.9.9.9"},
                    "ts": time.time() - 10_000}) + "\n",
        encoding="utf-8",
    )
    guard.poll_commands()
    assert guard._results["old1"]["ok"] is False
    assert "过期" in guard._results["old1"]["message"]


def test_publish_state_shape(control) -> None:
    guard, _calls, _allow, tmp = control
    guard.fw.set_members = lambda name: (
        [{"ip": "45.195.19.200", "expires_at": None}] if name == "tg_allow"
        else [{"ip": "203.0.113.9", "expires_at": time.time() + 100}]
    )
    guard.publish()
    state = json.loads((tmp / "guard-state.json").read_text(encoding="utf-8"))
    assert set(state) >= {"updated_at", "port", "allow", "banned", "counters", "results"}
    assert state["banned"][0]["ip"] == "203.0.113.9"
    assert state["allow"][0]["source"] == "learned"


def daemon_port_default(guard) -> int:
    return guard.port
