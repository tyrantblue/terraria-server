"""语义化版本比较（极简实现，够用即可）。

面板每次请求都会带 `X-Client-Version`，后端需要判断它是否低于
`min_client_version`（见 issue #17.3）。这里刻意不引入 `packaging`：

* 只处理 `major.minor.patch` 这种点分数字，允许 `v` 前缀与预发布后缀被忽略；
* 缺失的段按 0 补齐，所以 `1.4` == `1.4.0`，不会因为写法不同误判；
* 解析不出数字的段按 0 处理，绝不抛异常——一个畸形版本头不该让请求 500。
"""

from __future__ import annotations

__all__ = ["parse_version", "version_lt", "version_tuple", "looks_like_version"]


def looks_like_version(value: str | None) -> bool:
    """是否像是一个版本号（至少含一个数字）。

    这道门槛是**兼容性提示**而不是安全边界（攻击者直接不带这个头即可绕过），
    所以对 `dev` / `unknown` 这类非版本字符串应当放行，别把正常调用方拦在门外。
    """
    return bool(value) and any(char.isdigit() for char in str(value))


def parse_version(value: str | None) -> tuple[int, ...]:
    """把 `"1.4.1"` 解析成 `(1, 4, 1)`；无法解析的部分按 0。"""
    if not value:
        return (0,)
    text = str(value).strip().lstrip("vV")
    parts: list[int] = []
    for chunk in text.split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def version_tuple(value: str | None, *, length: int | None = None) -> tuple[int, ...]:
    """解析并按 `length` 补零，便于做「长度无关」的逐段比较。"""
    parsed = list(parse_version(value))
    if length is None:
        length = len(parsed)
    if len(parsed) < length:
        parsed.extend([0] * (length - len(parsed)))
    return tuple(parsed[:length])


def version_lt(left: str | None, right: str | None) -> bool:
    """`left < right`（语义化版本，长度无关）。"""
    left_parts = parse_version(left)
    right_parts = parse_version(right)
    size = max(len(left_parts), len(right_parts))
    return version_tuple(left, length=size) < version_tuple(right, length=size)
