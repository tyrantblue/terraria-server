"""状态缓存与重复轮询的行为测试。

这里专门覆盖一个曾经漏掉的回归：/status 第一次调用只做「初始化日志游标」，
第二次开始才会扫描新增日志行——如果那段代码与 LogReader 的返回签名不一致，
单次调用的测试是发现不了的。
"""

from __future__ import annotations

from app.services.status import StatusCollector


def test_repeated_status_calls_scan_new_log_lines(client) -> None:
    for _ in range(5):
        response = client.get("/api/server/status")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["version"] == "1.4.5.8"
        assert body["port"] == 7777
        assert body["max_players"] == 255
        assert body["time"] is not None


def test_status_uses_cache_within_ttl(rt) -> None:
    """TTL 内重复读取不应该反复发命令（这正是日志噪音的来源）。"""
    collector = StatusCollector(rt.channel, rt.reader, ttl=30.0, static_ttl=30.0)
    collector.get()
    size_after_first = rt.reader.size()
    for _ in range(5):
        collector.get()
    assert rt.reader.size() == size_after_first


def test_restart_marker_invalidates_cache(rt, fake_terraria) -> None:
    """日志里出现 'Listening on port' 说明服务端重启了，缓存必须作废并重新读取。"""
    collector = StatusCollector(rt.channel, rt.reader, ttl=30.0, static_ttl=300.0)
    collector.get()
    fetched_at = collector._static_at

    fake_terraria.inject_line("Listening on port 7777")
    collector.get()

    assert collector._static_at > fetched_at, "重启标记没有让静态缓存失效"
