"""Forced channel membership checks and subscription enforcement."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from redis.asyncio import Redis

from app.db import Database

logger = logging.getLogger("subio.channels")
CACHE_TTL = 300
VERIFY_CALLBACK = "channels:verify"


class ForcedChannelService:
    def __init__(self, db: Database, cache: Redis) -> None:
        self._db = db
        self._cache = cache

    async def list_active(self) -> list[dict[str, Any]]:
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT chat_id, username, title, invite_link
                        FROM forced_channels WHERE is_active ORDER BY id
                        """
                    )
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def check_membership(self, bot: Bot, user_id: int, *, use_cache: bool = True) -> tuple[bool, list[dict[str, Any]]]:
        channels = await self.list_active()
        if not channels:
            return True, []

        cache_key = f"channel:ok:{user_id}"
        if use_cache and await self._cache.get(cache_key) == "1":
            return True, []

        missing: list[dict[str, Any]] = []
        for channel in channels:
            chat_id = int(channel["chat_id"])
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                status = member.status
                if status == "restricted" and getattr(member, "is_member", False):
                    continue
                if status in {"left", "kicked"} or (
                    status == "restricted" and not getattr(member, "is_member", False)
                ):
                    missing.append(channel)
            except Exception:
                logger.warning("channel_check_failed", extra={"chat_id": chat_id, "user_id": user_id})
                missing.append(channel)

        if not missing:
            await self._cache.set(cache_key, "1", ex=CACHE_TTL)
            return True, []

        await self._cache.delete(cache_key)
        return False, missing

    async def join_keyboard(self, missing: list[dict[str, Any]]) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        for channel in missing:
            label = channel.get("title") or channel.get("username") or str(channel["chat_id"])
            link = channel.get("invite_link")
            if link:
                rows.append([InlineKeyboardButton(text=f"عضویت در {label}", url=str(link))])
            elif channel.get("username"):
                rows.append(
                    [InlineKeyboardButton(text=f"عضویت در {label}", url=f"https://t.me/{channel['username']}")]
                )
        rows.append([InlineKeyboardButton(text="✅ عضو شدم — بررسی مجدد", callback_data=VERIFY_CALLBACK)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def deactivate_subscriptions(self, user_id: int) -> int:
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            result = await conn.execute(
                text(
                    """
                    UPDATE subscriptions SET is_active=FALSE
                    WHERE user_id=:user_id AND is_active
                    RETURNING id
                    """
                ),
                {"user_id": user_id},
            )
            sub_ids = [str(row[0]) for row in result.fetchall()]
            for sub_id in sub_ids:
                await conn.execute(
                    text("UPDATE panel_clients SET is_active=FALSE WHERE subscription_id=:sub_id"),
                    {"sub_id": sub_id},
                )
        await self._cache.delete(f"channel:ok:{user_id}")
        if sub_ids:
            logger.info("subscriptions_deactivated", extra={"user_id": user_id, "count": len(sub_ids)})
        return len(sub_ids)

    async def enforce_active_subscribers(self, bot: Bot) -> int:
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT DISTINCT s.user_id
                        FROM subscriptions s
                        WHERE s.is_active AND s.expires_at > now()
                        """
                    )
                )
            ).fetchall()
        deactivated = 0
        for (user_id,) in rows:
            ok, _ = await self.check_membership(bot, int(user_id), use_cache=False)
            if not ok:
                deactivated += await self.deactivate_subscriptions(int(user_id))
        return deactivated
