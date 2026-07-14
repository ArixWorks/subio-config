"""AI-6: Daily admin digest."""

from __future__ import annotations

from typing import Any

from app.ai.gateway import AIGateway

SYSTEM = """Write a short Persian daily ops digest for SubIO admins (Telegram-ready, max 900 chars).
Include: healthy/dead configs, tests, users, tops issues, one recommended action.
No markdown tables. Use emoji sparingly."""


async def daily_digest(gateway: AIGateway, stats: dict[str, Any]) -> str:
    fallback = (
        f"📊 SubIO روزانه\n"
        f"سالم: {stats.get('healthy', 0)} | خراب: {stats.get('dead', 0)}\n"
        f"تست امروز: {stats.get('tests_today', 0)}\n"
        f"کاربران فعال: {stats.get('active_users', 0)}\n"
        f"گزارش‌ها: {stats.get('reports_today', 0)}"
    )
    if not gateway.enabled:
        return fallback
    text = await gateway.chat(
        system=SYSTEM,
        user=str(stats),
        tier="sol",
        max_tokens=500,
    )
    return text or fallback
