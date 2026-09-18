"""敏感字段的掩码与「这是不是回显的掩码」判定。

集中在一处的原因：`serverconfig.txt` 的 `password` 和通知配置里的
`client_secret` / webhook URL 都要「不回显明文、又允许客户端把读到的值原样写回来」，
规则必须一致，否则每加一个敏感字段就要重新吵一次。

约定：

* 回显用 `SECRET_MASK`（`••••••`）；没设置时回显空字符串。
* 写入时 `SECRET_MASK` 一律表示「保持不变」——掩码不可能是真实值，
  把它当值写进去只会把配置弄坏（见 issue #6 的讨论）。
* URL 这类「本身是凭据、但仍需要让人认出是哪个」的字段用 `mask_url()`：
  只留 scheme + host，并带上省略号，`is_mask_like()` 能识别出这种形态。
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: 敏感字段的回显占位符
SECRET_MASK = "••••••"
#: 掩码 URL 的结尾（`mask_url()` 的输出总以它收尾）
MASK_SUFFIX = "…"


def secret_is_set(value: object) -> bool:
    """该敏感字段是否已设置（空/None 都算没设置）。"""
    return bool(str(value or "").strip())


def mask_secret(value: object) -> str:
    """已设置 → 掩码；未设置 → 空字符串。"""
    return SECRET_MASK if secret_is_set(value) else ""


def mask_url(url: str) -> str:
    """只露出主机名——webhook URL 本身就是凭据，不能原样回给前端。"""
    if not url:
        return ""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/{MASK_SUFFIX}" if parts.netloc else MASK_SUFFIX


def is_mask_like(value: object, *, current: str = "") -> bool:
    """这个值是不是「读到的掩码被原样写回来」？"""
    text = str(value if value is not None else "").strip()
    if not text:
        return False
    if text == SECRET_MASK:
        return True
    return bool(current) and text in (mask_url(current), mask_secret(current))


def resolve_secret(value: object, current: str) -> str | None:
    """把请求里带来的敏感字段解析成「要写什么」。

    返回 `None` 表示**保持不变**；返回 `""` 表示显式清空；其余是真实新值。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return ""
    if is_mask_like(text, current=current):
        return None
    return text
