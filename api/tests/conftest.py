"""pytest 公共 fixture：一个不依赖真实 Terraria 的运行时环境。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app
from app.services.runtime import Runtime, build_runtime
from tests.fake_terraria import SENTINEL, FakeTerraria

STARTUP_LOG = """Error Logging Enabled.
Terraria Server v1.4.5.8

Listening on port 7777
: Server started
"""


@pytest.fixture
def fake_terraria(tmp_path: Path) -> FakeTerraria:
    """一套临时目录里的假服务端：worlds/ + serverconfig.txt + control/。"""
    worlds = tmp_path / "worlds"
    worlds.mkdir()
    (worlds / "WSD.wld").write_bytes(b"W" * 128)
    (worlds / "gogogo.wld").write_bytes(b"G" * 256)

    (tmp_path / "serverconfig.txt").write_text(
        "autocreate=3\n"
        "world=/worlds/WSD.wld\n"
        "worldname=WSD\n"
        "# 注释应当被保留\n"
        "maxplayers=255\n"
        "port=7777\n",
        encoding="utf-8",
    )

    control = tmp_path / "control"
    control.mkdir()
    os.mkfifo(control / "command.fifo")
    (control / "output.log").write_text(STARTUP_LOG, encoding="utf-8")

    server = FakeTerraria(control / "command.fifo", control / "output.log", sentinel=SENTINEL)
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def settings(tmp_path: Path, fake_terraria: FakeTerraria) -> Settings:
    # 所有会被写入的路径都必须落在 tmp_path 里，否则测试会污染线上目录
    (tmp_path / "backup").mkdir(exist_ok=True)
    return Settings(
        worlds_dir=tmp_path / "worlds",
        config_file=tmp_path / "serverconfig.txt",
        control_dir=tmp_path / "control",
        backup_dir=tmp_path / "backup",
        status_ttl=0.0,  # 测试里每次都刷新，避免缓存掩盖问题
        static_ttl=0.0,
        console_timeout=3.0,
        console_lock_timeout=3.0,
        # 测试里不启动后台调度线程（要测就显式 run_now）
        schedule_enabled=False,
        # 测试里也不启动资源采样线程（要测就显式 sample() / 单独开）
        metrics_interval_seconds=0.0,
    )


@pytest.fixture
def rt(settings: Settings) -> Runtime:
    return build_runtime(settings)


@pytest.fixture
def client(rt: Runtime) -> TestClient:
    # create_app(rt)：依赖注入与生命周期用同一个 runtime，不会碰到真实目录
    with TestClient(create_app(rt)) as test_client:
        yield test_client
