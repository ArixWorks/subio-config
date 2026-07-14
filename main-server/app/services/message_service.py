"""System message templates (timeout text, cooldowns)."""

from __future__ import annotations

from app.db import Database


class MessageService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str, default: str = "") -> str:
        row = await self._db.fetch_one(
            "SELECT value FROM system_messages WHERE key=:key",
            {"key": key},
        )
        return str(row["value"]) if row else default

    async def cooldown_seconds(self) -> int:
        raw = await self.get("retry_cooldown_sec", "30")
        try:
            return max(5, min(300, int(raw)))
        except ValueError:
            return 30

    async def tester_timeout_message(self) -> str:
        return await self.get(
            "tester_timeout",
            "در حال حاضر به دلیل اختلال موقت در ارتباط تست‌کننده‌ها، "
            "امکان ساخت / به‌روزرسانی کانفیگ وجود ندارد. لطفاً چند دقیقه دیگر دوباره تلاش کنید.",
        )
