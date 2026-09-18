"""issue #14：`capabilities` 要覆盖面板全部页面，并给出「引入版本」。

面板的兼容性策略是「capabilities 里没有的能力一律当不存在」；守卫/定时任务/
备份/通知四个页面以前没有对应能力项，只能靠请求失败后再显示错误。
"""

from __future__ import annotations

from app.api.system import CAPABILITIES, CAPABILITY_SINCE
from app.services.operations import EXCLUSIVE_KINDS


def test_capabilities_cover_the_four_missing_panels(client) -> None:
    body = client.get("/api/meta").json()
    for name in ("guard.state", "scheduler.jobs", "backups.list", "notifications.status"):
        assert name in body["capabilities"], name


def test_every_capability_has_an_introduced_version(client) -> None:
    body = client.get("/api/meta").json()
    assert set(body["capability_since"]) == set(body["capabilities"])
    assert body["capability_since"]["guard.state"] == "2.1.0"
    assert body["capability_since"]["world.metadata"] == "2.0.0"


def test_capabilities_and_since_do_not_drift() -> None:
    """常量层面锁住「漏登记」：两边键集合必须一致且无重复。"""
    assert set(CAPABILITIES) == set(CAPABILITY_SINCE)
    assert len(CAPABILITIES) == len(set(CAPABILITIES))


def test_meta_publishes_the_operation_vocabulary(client) -> None:
    """后端自己描述自己的词汇表（issue #18 第 4 点），面板不硬编码映射。"""
    body = client.get("/api/meta").json()
    kinds = {item["kind"]: item["label"] for item in body["operation_kinds"]}
    assert kinds["server.restart"] == "Restart server"
    assert kinds["world.restore"] == "Restore backup"
    assert set(body["exclusive_operation_kinds"]) == set(EXCLUSIVE_KINDS)
