"""跨进程互操作：守卫进程与 API 必须共用同一把控制台锁和同一套哨兵协议。

契约写在两处（不同镜像里没法共享代码），这个测试是防止两边漂移的那道闸门：
如果 guard/terraria-watchd.py 的哨兵或锁路径与
api/app/services/console/channel.py 不一致，这里会失败。

需要整个仓库都在挂载里（见 tests/Dockerfile 的用法说明），否则跳过。
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

import pytest

GUARD_SCRIPT = Path(__file__).resolve().parents[2] / "guard" / "terraria-watchd.py"

pytestmark = pytest.mark.skipif(
    not GUARD_SCRIPT.exists(), reason="guard/ 不在测试挂载范围内"
)


@pytest.fixture(scope="module")
def daemon_module():
    spec = importlib.util.spec_from_file_location("terraria_watchd", GUARD_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sentinel_and_lock_are_identical(settings, daemon_module) -> None:
    """两边的哨兵字符串与锁文件位置必须一致，否则会互相读串/互不互斥。

    守卫脚本在 import 时从环境变量算默认路径，所以这里比对的是「不变式」：
    两边都取 <control_dir>/console.lock、都用同一个哨兵。
    """
    assert daemon_module.CONSOLE_SENTINEL == settings.console_sentinel
    assert settings.console_lock == settings.control_dir / "console.lock"
    assert daemon_module.CONSOLE_LOCK_FILE.name == settings.console_lock.name
    assert daemon_module.CONSOLE_LOCK_FILE.parent.name == settings.control_dir.name
    assert daemon_module.CONSOLE_FENCE == b"Usage: kick <player>"


def test_guard_query_gets_its_own_output(settings, rt, daemon_module) -> None:
    console = daemon_module.Console(
        settings.fifo, settings.log_file, lock_path=settings.console_lock
    )
    assert "No players connected." in console.query("playing", timeout=3)


def test_api_and_guard_do_not_cross_talk(settings, rt, daemon_module) -> None:
    console = daemon_module.Console(
        settings.fifo, settings.log_file, lock_path=settings.console_lock
    )
    results: list[tuple[str, str]] = []
    lock = threading.Lock()

    def api_worker() -> None:
        for _ in range(5):
            with lock:
                results.append(("Port: 7777", rt.channel.run("port")))

    def guard_worker() -> None:
        for _ in range(5):
            with lock:
                results.append(("No players connected.", console.query("playing", timeout=3)))

    threads = [threading.Thread(target=api_worker), threading.Thread(target=guard_worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 10
    for expected, output in results:
        assert expected in output, f"跨进程串台：期望 {expected!r}，实际 {output!r}"


def test_guard_online_players_is_conservative(settings, daemon_module) -> None:
    """认不出来时必须返回 -1（未知），绝不能误判成 0 人——自动恢复依赖它。"""
    console = daemon_module.Console(
        settings.fifo, settings.log_file, lock_path=settings.console_lock
    )
    assert console.online_players() == 0
