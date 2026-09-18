"""控制台通道的回归测试。

重点是旧设计里最脆弱的地方：并发时互相读到对方的回显。
"""

from __future__ import annotations

import threading

import pytest

from app.core.errors import ConsoleTimeout
from app.services.console.channel import ConsoleChannel
from app.services.console.parser import FENCE_TEXT, is_fence_line


def test_run_returns_only_the_command_output(rt) -> None:
    assert "Terraria Server v1.4.5.8" in rt.channel.run("version")
    assert "Port: 7777" in rt.channel.run("port")


def test_sentinel_line_is_filtered_from_the_console_view(rt) -> None:
    rt.channel.run("version")
    raw_lines = rt.reader.tail(500)
    # 哨兵行确实写进了日志……
    assert any(FENCE_TEXT in line for line in raw_lines)
    # ……但按文本可以被干净地过滤掉（面板、守卫进程、重启前的旧行都适用）
    visible = [line for line in raw_lines if not is_fence_line(line)]
    assert not any(FENCE_TEXT in line for line in visible)
    assert any("Terraria Server" in line for line in visible)


def test_send_does_not_produce_console_noise(rt) -> None:
    rt.channel.send("save")
    assert not any(FENCE_TEXT in line for line in rt.reader.tail(500))


def test_probe_does_not_add_console_noise(rt) -> None:
    """探活只写哨兵：日志里多出来的只有哨兵行（及提示符），面板看不到。"""
    before = len(rt.reader.tail(500))
    rt.channel.probe(timeout=1.0)
    new_lines = rt.reader.tail(500)[before:]
    assert new_lines, "探活应该至少写出一行哨兵"
    assert all(is_fence_line(line) or not line.strip(": ") for line in new_lines), new_lines


def test_probe_times_out_when_the_pipeline_is_stalled(rt, fake_terraria) -> None:
    """哨兵没出现时必须报错，不能像 run() 那样回退成"返回部分输出"。"""
    fake_terraria.swallow_output = True
    with pytest.raises(ConsoleTimeout):
        rt.channel.probe(timeout=0.3)


def test_concurrent_commands_do_not_cross_talk(settings, rt) -> None:
    """两个通道实例（模拟 API 与守卫进程）共享同一把文件锁，互不串台。"""
    other = ConsoleChannel(
        fifo=settings.fifo,
        reader=rt.reader,
        lock_path=settings.console_lock,
        sentinel=settings.console_sentinel,
        timeout=3.0,
        lock_timeout=5.0,
    )

    results: list[tuple[str, str]] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker(channel: ConsoleChannel, command: str, expected: str) -> None:
        for _ in range(5):
            try:
                output = channel.run(command)
            except Exception as exc:  # noqa: BLE001 - 收集后统一断言
                with lock:
                    errors.append(f"{command}: {exc!r}")
                continue
            with lock:
                results.append((expected, output))

    threads = [
        threading.Thread(target=worker, args=(rt.channel, "version", "Terraria Server v1.4.5.8")),
        threading.Thread(target=worker, args=(other, "port", "Port: 7777")),
        threading.Thread(target=worker, args=(rt.channel, "maxplayers", "Player limit: 255")),
        threading.Thread(target=worker, args=(other, "playing", "No players connected.")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    assert len(results) == 20
    for expected, output in results:
        # 旧实现会把别的命令的回显混进来；这里每条回显必须只含自己的内容
        assert expected in output, f"串台：期望 {expected!r}，实际 {output!r}"


def test_server_generated_lines_do_not_break_parsing(rt, fake_terraria) -> None:
    """命令窗口里混进服务端自己的日志行时，定向解析仍然正确。"""
    fake_terraria.inject_line("203.0.113.9:1234 is connecting...")
    assert "Port: 7777" in rt.channel.run("port")
