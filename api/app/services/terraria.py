from pathlib import Path
import time


FIFO = Path("/opt/terraria/control/command.fifo")
LOG = Path("/opt/terraria/control/output.log")


def send_command(command: str) -> None:
    command = command.strip()

    if not command:
        raise ValueError("command cannot be empty")

    if "\n" in command or "\r" in command:
        raise ValueError("command must be a single line")

    with FIFO.open("w") as fifo:
        fifo.write(command + "\n")
        fifo.flush()


def get_recent_output(lines: int = 50) -> list[str]:
    if not LOG.exists():
        return []

    with LOG.open("r", encoding="utf-8", errors="replace") as f:
        return [line.rstrip("\n") for line in f.readlines()[-lines:]]


def get_log_size() -> int:
    if not LOG.exists():
        return 0

    return LOG.stat().st_size


def read_new_output(
    previous_size: int,
    timeout: float = 2.0,
) -> str:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if LOG.exists() and LOG.stat().st_size > previous_size:
            with LOG.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(previous_size)
                return f.read()

        time.sleep(0.05)

    return ""


def execute_command(
    command: str,
    timeout: float = 2.0,
) -> str:
    previous_size = get_log_size()

    send_command(command)

    return read_new_output(previous_size, timeout)
