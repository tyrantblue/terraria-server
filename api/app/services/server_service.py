"""服务端用例：把「控制台命令」翻译成业务动作。

这一层不 import fastapi，也不碰 HTTP 状态码——只抛 core.errors 里的领域异常。
旧实现把校验、命令拼接、响应拼装全塞在 routers/server.py 的 461 行里。

单向命令（save/settle/kick/ban/say/motd/password/maxplayers/time）走 channel.send()，
与旧实现一样是「发完即返回」；只有真正需要读取回显的地方（状态查询、重启探测）
才走 channel.run()。
"""

from __future__ import annotations

import time

from app.core.errors import BadRequest, Conflict
from app.services import config_service
from app.services.config_service import ConfigService
from app.services.console.channel import ConsoleChannel
from app.services.operations import Operation, OperationRegistry
from app.services.status import ServerSnapshot, StatusCollector

TIME_PHASES = {"dawn", "noon", "dusk", "midnight"}

#: 发 exit 之后等待服务端重新可用的最长时间（秒）
RESTART_TIMEOUT = 60.0
#: exit 命令本身带保存；先单独 save 一次，给落盘留点时间
SAVE_GRACE = 1.0


def _single_line(value: str, *, field: str) -> str:
    value = value.strip()
    if not value:
        # 旧实现是 HTTPException(400, ...)，保持一致
        raise BadRequest(f"{field} cannot be empty")
    if "\n" in value or "\r" in value:
        raise BadRequest(f"{field} must be a single line")
    return value


class ServerService:
    def __init__(
        self,
        channel: ConsoleChannel,
        status: StatusCollector,
        config: ConfigService,
        operations: OperationRegistry,
    ) -> None:
        self._channel = channel
        self._status = status
        self._config = config
        self._operations = operations

    # -- 只读 ---------------------------------------------------------
    def status(self) -> ServerSnapshot:
        return self._status.get()

    def players(self) -> list[str]:
        return self._status.players()

    # -- 通用命令 -----------------------------------------------------
    def run_command(self, command: str) -> str:
        """透传一条控制台命令。

        旧实现是 fire-and-forget（send_command），这里保持同样的语义：
        面板发 `save`、`exit` 这类会阻塞/终止服务端的命令时不会被误判为超时。
        """
        command = command.strip()
        if not command:
            # 旧实现会走到 HTTPException(500)；这里修正为 400，见 CHANGELOG
            raise BadRequest("command cannot be empty")
        if "\n" in command or "\r" in command:
            raise BadRequest("command must be a single line")
        self._channel.send(command)
        # 任何命令都可能改到 motd/上限/时间/玩家，保守地把缓存作废
        self._status.invalidate()
        return command

    def save(self) -> None:
        self._channel.send("save")

    def settle(self) -> None:
        self._channel.send("settle")

    def set_time_phase(self, phase: str) -> str:
        if phase not in TIME_PHASES:
            raise BadRequest(f"phase must be one of {sorted(TIME_PHASES)}")
        self._channel.send(phase)
        self._status.invalidate(dynamic=True)
        return phase

    # -- 广播 / 玩家 ---------------------------------------------------
    def say(self, message: str) -> str:
        message = _single_line(message, field="message")
        self._channel.send(f"say {message}")
        return message

    def kick(self, player: str) -> str:
        player = _single_line(player, field="player")
        self._channel.send(f"kick {player}")
        self._status.invalidate(dynamic=True)
        return player

    def ban(self, player: str) -> str:
        player = _single_line(player, field="player")
        self._channel.send(f"ban {player}")
        self._status.invalidate(dynamic=True)
        return player

    # -- 运行时可调项（不落盘，与旧行为一致） ----------------------------
    def set_max_players(self, value: int) -> int:
        self._channel.send(f"maxplayers {value}")
        self._status.invalidate(static=True, dynamic=False)
        return value

    def set_motd(self, motd: str) -> str:
        motd = _single_line(motd, field="motd")
        self._channel.send(f"motd {motd}")
        self._status.invalidate(static=True, dynamic=False)
        return motd

    def set_password(self, password: str) -> None:
        # 旧实现漏了非空校验，`{"password": ""}` 能把服务器密码清掉
        if "\n" in password or "\r" in password:
            raise BadRequest("password must be a single line")
        if not password.strip():
            raise BadRequest("password cannot be empty")
        self._channel.send(f"password {password}")

    # -- 重启（长任务） -------------------------------------------------
    def restart(self) -> Operation:
        """保存并让容器重启服务端；返回后台操作。"""
        return self._operations.submit("server.restart", self._restart_job)

    # -- 定时任务用的入口 ------------------------------------------------
    def scheduled_save(self, skip_if_empty: bool) -> str:
        """定时保存。默认没人在线就跳过——省掉一次 12MB 的无意义落盘。"""
        if skip_if_empty:
            try:
                online = len(self._status.player_entries())
            except Exception:  # noqa: BLE001 - 控制台暂时不可用时不要假装成功
                return "skipped: 控制台不可用"
            if online == 0:
                return "skipped: 无人在线"
        self._channel.send("save")
        return "saved"

    def scheduled_restart(self, *, skip_if_players: bool, warn_minutes: int) -> str:
        """定时重启。默认有人在线就跳过，避免把正在玩的人踢下线。"""
        online = 0
        try:
            online = len(self._status.player_entries())
        except Exception:  # noqa: BLE001
            online = 0
        if skip_if_players and online > 0:
            if warn_minutes > 0:
                self._channel.send(
                    f"say [server] scheduled restart skipped: {online} player(s) online"
                )
            return f"skipped: {online} 人在线"
        if warn_minutes > 0:
            self._channel.send(
                f"say [server] scheduled restart: saving world now, back in ~1 minute"
            )
        operation = self.restart()
        return f"submitted: {operation.id}"

    def _restart_job(self, progress) -> dict[str, object]:
        progress(5, "正在保存世界")
        self._channel.send("save")
        time.sleep(SAVE_GRACE)
        progress(25, "正在关闭服务端")
        self._channel.send("exit")
        self._wait_until_up(progress)
        return {"restarted": True}

    def _wait_until_up(self, progress, timeout: float = RESTART_TIMEOUT) -> None:
        """轮询 version 直到服务端重新可用；重启期间 FIFO 会短暂消失。"""
        deadline = time.monotonic() + timeout
        step = 0
        while time.monotonic() < deadline:
            try:
                if "Terraria Server" in self._channel.run("version", timeout=2.0):
                    self._status.invalidate()
                    progress(95, "服务端已恢复")
                    return
            except Exception:  # noqa: BLE001 - 重启窗口内允许失败
                pass
            step += 1
            progress(min(90, 30 + step * 5), "等待服务端重新监听")
            time.sleep(1)
        raise TimeoutError(f"服务端在 {int(timeout)} 秒内没有恢复")

    # -- 配置：持久化 + 可选立即生效 --------------------------------------
    def update_config(
        self,
        values: dict[str, object],
        *,
        apply: bool,
        confirm_low_max_players: bool,
    ) -> dict[str, object]:
        normalized = config_service.validate(values)
        if not normalized:
            raise BadRequest("没有需要修改的配置项")

        if (
            "maxplayers" in normalized
            and config_service.is_low_max_players(normalized["maxplayers"])
            and not confirm_low_max_players
        ):
            raise Conflict(
                "maxplayers 设得太小会被端口扫描「假满员」拖垮：原版会把每条陌生 TCP "
                "连接都算进名额。确认要这么做就带上 confirm_low_max_players=true。",
                details={"requested": int(normalized["maxplayers"]),
                         "recommended_min": config_service.LOW_MAX_PLAYERS,
                         "reason": "phantom-full"},
            )

        before = self._config.load()
        changed = {k: v for k, v in normalized.items() if before.get(k) != v}
        self._config.set_many(normalized)

        result: dict[str, object] = {
            "persisted": sorted(normalized),
            "changed": sorted(changed),
            "applied": [],
            "requires_restart": [],
            "operation_id": None,
        }
        if not changed:
            self._status.invalidate(static=True)
            return result

        restart_needed = sorted(set(changed) & config_service.RESTART_KEYS)
        result["requires_restart"] = restart_needed

        if apply:
            # 复用各自的校验/生效路径，避免在这里手拼控制台命令
            applied: list[str] = []
            if "motd" in changed:
                self.set_motd(changed["motd"])
                applied.append("motd")
            if "password" in changed:
                self.set_password(changed["password"])
                applied.append("password")
            if "maxplayers" in changed:
                self.set_max_players(int(changed["maxplayers"]))
                applied.append("maxplayers")
            result["applied"] = applied
            self._status.invalidate(static=True)

        if apply and restart_needed:
            operation = self.restart()
            result["operation_id"] = operation.id
        return result
