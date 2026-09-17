"""服务端用例：把「控制台命令」翻译成业务动作。

这一层不 import fastapi，也不碰 HTTP 状态码——只抛 core.errors 里的领域异常。
旧实现把校验、命令拼接、响应拼装全塞在 routers/server.py 的 461 行里。

单向命令（save/settle/kick/ban/say/motd/password/maxplayers/time）走 channel.send()，
与旧实现一样是「发完即返回」；只有真正需要读取回显的地方（状态查询、重启探测）
才走 channel.run()。
"""

from __future__ import annotations

from app.core.errors import BadRequest
from app.services.config_service import ConfigService
from app.services.console.channel import ConsoleChannel
from app.services.status import ServerSnapshot, StatusCollector

TIME_PHASES = {"dawn", "noon", "dusk", "midnight"}


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
    ) -> None:
        self._channel = channel
        self._status = status
        self._config = config

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
