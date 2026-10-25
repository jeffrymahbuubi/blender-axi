"""Text truncation helper (plan section 3.3)."""

from __future__ import annotations

from .spec import TRUNCATE_CHARS


def truncate(s: str, limit: int = TRUNCATE_CHARS, full: bool = False) -> tuple[str, int, bool]:
    """Return (shown, total_chars, truncated). Never cuts when `full` is true or s fits in `limit`.

    Callers append "... (truncated, N chars total)" and a --full hint only when `truncated`.
    """
    total = len(s)
    if full or total <= limit:
        return s, total, False
    return s[: max(limit, 0)], total, True
