"""极简埋点：统计被弃用接口的调用量。

目的只有一个——**用数据决定什么时候可以删旧接口**，而不是拍一个日期。
写在内存里（进程重启即清零，够用），通过 `GET /api/meta/usage` 暴露。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Usage:
    path: str
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    client_versions: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "client_versions": dict(self.client_versions),
        }


class Telemetry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[str, Usage] = {}

    def record(self, path: str, client_version: str | None) -> None:
        now = time.time()
        with self._lock:
            usage = self._usage.get(path)
            if usage is None:
                usage = Usage(path=path, first_seen=now)
                self._usage[path] = usage
            usage.count += 1
            usage.last_seen = now
            key = client_version or "unknown"
            usage.client_versions[key] = usage.client_versions.get(key, 0) + 1

    def snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                usage.as_dict()
                for usage in sorted(self._usage.values(), key=lambda u: -u.count)
            ]

    def reset(self) -> None:
        with self._lock:
            self._usage.clear()


telemetry = Telemetry()
