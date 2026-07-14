"""Shared formatting helpers for user-facing text."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def format_bytes(num: int | float | None) -> str:
    """Format byte counts for humans (B / KB / MB / GB / TB)."""
    try:
        value = float(num or 0)
    except (TypeError, ValueError):
        value = 0.0
    if value < 0:
        value = 0.0
    units = ("B", "KB", "MB", "GB", "TB")
    size = value
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    if size >= 100 or abs(size - round(size)) < 0.05:
        return f"{size:.0f} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"


def format_volume_pair(used: Any, limit: Any) -> str:
    return f"{format_bytes(used)} / {format_bytes(limit)}"


def format_expiry(value: Any, language: str = "fa") -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return "—"
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text[:19]
    # Show UTC date-time without microseconds
    stamp = dt.strftime("%Y-%m-%d %H:%M UTC")
    if language.startswith("fa"):
        return stamp
    return stamp
