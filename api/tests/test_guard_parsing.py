"""守卫进程对新日志格式（start.sh 加了 [时间戳]）必须照样工作。

日志格式变更曾经是最容易"静默失效"的地方：正则不匹配 → 封禁/学习不再生效，
但日志里看不出任何异常。这个文件就是为了盯住这一点。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

GUARD_SCRIPT = Path(__file__).resolve().parents[2] / "guard" / "terraria-watchd.py"
pytestmark = pytest.mark.skipif(not GUARD_SCRIPT.exists(), reason="guard/ 不在测试范围内")

TS = "[2026-09-17 17:01:02] "


@pytest.fixture(scope="module")
def daemon():
    spec = importlib.util.spec_from_file_location("terraria_watchd", GUARD_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("prefix", ["", TS, ": ", TS + ": "])
def test_line_regexes_accept_both_formats(daemon, prefix) -> None:
    assert daemon.RE_CONNECT.match(f"{prefix}203.0.113.9:1234 is connecting...")
    assert daemon.RE_LOST.match(f"{prefix}203.0.113.9:1234 lost connection...")
    booted = daemon.RE_BOOTED.match(f"{prefix}1.2.3.4:5 was booted: Invalid operation at this state.")
    assert booted and booted.group("ip") == "1.2.3.4"
    joined = daemon.RE_JOINED.match(f"{prefix}ユノの犬 has joined.")
    assert joined and joined.group("name") == "ユノの犬"
    assert daemon.RE_LISTENING.search(f"{prefix}Listening on port 7777")


@pytest.mark.parametrize("prefix", ["", TS, ": ", TS + ": "])
def test_player_line_accepts_both_formats(daemon, prefix) -> None:
    match = daemon.RE_PLAYER_LINE.match(f"{prefix}CTQ (198.51.100.30:44176)")
    assert match and match.group("name") == "CTQ" and match.group("ip") == "198.51.100.30"


def test_connect_then_drop_still_produces_a_strike(daemon, tmp_path) -> None:
    """带时间戳的日志也要能触发封禁逻辑（这是守卫的核心功能）。"""
    import argparse

    settings = argparse.Namespace(
        ban=True, recover=False, no_learn=True, dry_run=False, backfill=False,
        once=True, verbose=False,
    )
    watchdog = daemon.Watchdog(settings)
    sent: list[list[str]] = []
    watchdog.fw._run = lambda argv: sent.append(argv) or True

    daemon.STRIKES = 3
    for i in range(4):
        watchdog.handle(f"{TS}203.0.113.9:{1000 + i} is connecting...", 1)
        watchdog.handle(f"{TS}203.0.113.9:{1000 + i} lost connection...", 2)

    assert sent, "带时间戳的日志没有触发封禁"
    assert "203.0.113.9" in sent[0]


def test_learning_still_sees_join_lines(daemon, tmp_path) -> None:
    """学习型白名单依赖 has joined.，格式变化后不能失效。"""
    import argparse

    settings = argparse.Namespace(
        ban=True, recover=False, no_learn=False, dry_run=False, backfill=False,
        once=True, verbose=False,
    )
    watchdog = daemon.Watchdog(settings)
    watchdog.handle(f"{TS}198.51.100.7:1 is connecting...", 1)
    watchdog.handle(f"{TS}Alice has joined.", 2)
    assert watchdog.learn_queue, "带时间戳的 has joined. 没有被识别"
    assert watchdog.learn_queue[0][0] == "Alice"
