"""旧接口的弃用登记表。

集中在一处声明「哪条旧路由被什么取代、什么时候下线」，好处是：
* 中间件据此自动给响应加 `Deprecation` / `Sunset` / `Link` 头；
* `GET /api/meta` 能把当前弃用项直接告诉前端；
* 埋点按同一张表统计用量，用数据判断「前端已经迁完」再删。

新增/删除弃用项都在这里改，不要散落到各个 router。
"""

from __future__ import annotations

from dataclasses import dataclass

#: 弃用接口的统一下线日期（ISO）。到点前需要先看 /api/meta/usage 的调用量。
SUNSET = "2026-11-16"
SINCE = "1.2.0"
SUNSET_HTTP = "Mon, 16 Nov 2026 00:00:00 GMT"


@dataclass(frozen=True)
class Deprecation:
    path: str
    replacement: str
    since: str = SINCE
    sunset: str = SUNSET

    def headers(self) -> dict[str, str]:
        return {
            "Deprecation": "true",
            "Sunset": SUNSET_HTTP,
            "Link": f'<{self.replacement}>; rel="successor-version"',
            "X-API-Deprecated": f"{self.path} -> {self.replacement} (sunset {self.sunset})",
        }

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "replacement": self.replacement,
            "since": self.since,
            "sunset": self.sunset,
        }


def _d(path: str, replacement: str) -> tuple[str, Deprecation]:
    return path, Deprecation(path=path, replacement=replacement)


#: 旧路径 → 替代品。路径必须是完整的 URL path（不带 query）。
DEPRECATIONS: dict[str, Deprecation] = dict(
    [
        _d("/api/server/status", "/api/v1/server"),
        _d("/api/server/players", "/api/v1/players"),
        _d("/api/server/console", "/api/v1/console"),
        _d("/api/server/ws", "/api/v1/console/stream"),
        _d("/api/server/command", "/api/v1/console/commands"),
        _d("/api/server/say", "/api/v1/broadcast"),
        _d("/api/server/kick", "/api/v1/players/{name}/kick"),
        _d("/api/server/ban", "/api/v1/players/{name}/ban"),
        _d("/api/server/maxplayers", "/api/v1/config"),
        _d("/api/server/motd", "/api/v1/config"),
        _d("/api/server/password", "/api/v1/config"),
        _d("/api/server/port", "/api/v1/server"),
        _d("/api/server/version", "/api/v1/server"),
        _d("/api/server/playing", "/api/v1/players"),
        _d("/api/server/save", "/api/v1/server/actions"),
        _d("/api/server/settle", "/api/v1/server/actions"),
        _d("/api/server/time/dawn", "/api/v1/server/time"),
        _d("/api/server/time/noon", "/api/v1/server/time"),
        _d("/api/server/time/dusk", "/api/v1/server/time"),
        _d("/api/server/time/midnight", "/api/v1/server/time"),
        _d("/api/world/list", "/api/v1/worlds"),
        _d("/api/world/upload", "/api/v1/worlds"),
        _d("/api/world/switch", "/api/v1/worlds/{file}/activate"),
    ]
)


def lookup(path: str) -> Deprecation | None:
    return DEPRECATIONS.get(path)


def all_deprecations() -> list[dict[str, str]]:
    return [dep.as_dict() for dep in sorted(DEPRECATIONS.values(), key=lambda d: d.path)]
