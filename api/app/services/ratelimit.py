"""最基础的写操作限流（issue #17.2）。

`main.py` 里早就把 `429: "too_many_requests"` 映射好了，但全仓库没有任何限流实现，
这个映射一直是死代码。控制台命令是串行锁 + 3 秒超时，密集的误操作/恶意请求会把
控制台一直占住并刷屏审计，所以这里按「桶 × 客户端」做滑动窗口限流。

设计取舍：

* **进程内内存**：这是单进程的管理面板，量级小；多副本部署应改用 Redis 之类，
  文档里写明了这个前提（见 docs/api/v1.md §0）。
* **滑动窗口（deque 时间戳）**：比固定窗口更平滑，实现也就十来行。
* 桶由调用方按路径决定（写操作 / 控制台命令 / 重启），`limit <= 0` 表示不限流。
* `check()` 返回 0 表示放行，否则返回建议的 `Retry-After` 秒数。
"""

from __future__ import annotations

import threading
import time
from collections import deque

#: 限流窗口（秒）。所有桶共用 60 秒，文档与 `Retry-After` 都按这个来。
WINDOW_SECONDS = 60.0

#: 超过这个数量的空桶会在一次新请求到来时被顺手清理，避免长期运行内存只增不减。
_PRUNE_THRESHOLD = 4096


class RateLimiter:
    def __init__(self, window: float = WINDOW_SECONDS) -> None:
        self._window = window
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, bucket: str, key: str, limit: int) -> float:
        """记一次命中。返回 0 表示放行；否则是建议等待的秒数。"""
        if limit <= 0:
            return 0.0

        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            if len(self._hits) > _PRUNE_THRESHOLD:
                self._prune(cutoff)
            hits = self._hits.get((bucket, key))
            if hits is None:
                hits = deque()
                self._hits[(bucket, key)] = hits
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return max(0.001, self._window - (now - hits[0]))
            hits.append(now)
            return 0.0

    def _prune(self, cutoff: float) -> None:
        """清掉窗口外/已空的桶。调用方必须已持锁。"""
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
