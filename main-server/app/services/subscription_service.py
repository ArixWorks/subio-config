"""Subscription lifecycle, daily volume, and referral handling."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db import Database
from app.services.panel_service import DAILY_BASE_BYTES, PanelService, REFERRAL_BONUS_BYTES


class SubscriptionService:
    def __init__(self, db: Database, panels: PanelService) -> None:
        self._db = db
        self._panels = panels

    async def ensure_user(self, telegram_id: int, username: str | None, language: str) -> None:
        await self._db.execute(
            """
            INSERT INTO users(telegram_id, username, language)
            VALUES (:id, :username, :language)
            ON CONFLICT (telegram_id) DO UPDATE SET username=excluded.username
            """,
            {"id": telegram_id, "username": username, "language": language},
        )

    async def register_referral(self, user_id: int, referrer_id: int) -> None:
        if user_id == referrer_id:
            return
        row = await self._db.fetch_one(
            "SELECT referred_by FROM users WHERE telegram_id=:id",
            {"id": user_id},
        )
        if row and row.get("referred_by"):
            return
        await self._db.execute(
            "UPDATE users SET referred_by=:referrer WHERE telegram_id=:id AND referred_by IS NULL",
            {"referrer": referrer_id, "id": user_id},
        )
        await self._panels.apply_referral_bonus(referrer_id)

    async def create_daily_subscription(self, user_id: int) -> dict[str, Any]:
        existing = await self._db.fetch_one(
            """
            SELECT id, token, expires_at, volume_limit_bytes, volume_used_bytes
            FROM subscriptions
            WHERE user_id=:user_id AND is_active AND expires_at > now()
            ORDER BY expires_at DESC LIMIT 1
            """,
            {"user_id": user_id},
        )
        if existing:
            return dict(existing)
        user = await self._db.fetch_one(
            "SELECT referral_credit_bytes FROM users WHERE telegram_id=:id",
            {"id": user_id},
        )
        bonus = int(user["referral_credit_bytes"]) if user else 0
        volume = DAILY_BASE_BYTES + bonus
        token = uuid.uuid4()
        expires = datetime.now(tz=UTC) + timedelta(days=1)
        sub_id = str(uuid.uuid4())
        await self._db.execute(
            """
            INSERT INTO subscriptions(id, token, user_id, volume_limit_bytes, expires_at)
            VALUES (:id, :token, :user_id, :volume, :expires)
            """,
            {
                "id": sub_id,
                "token": token,
                "user_id": user_id,
                "volume": volume,
                "expires": expires,
            },
        )
        if bonus:
            await self._db.execute(
                "UPDATE users SET referral_credit_bytes=0 WHERE telegram_id=:id",
                {"id": user_id},
            )
        panel_info = await self._panels.create_private_client(
            subscription_id=sub_id, user_id=user_id
        )
        return {
            "id": sub_id,
            "token": token,
            "expires_at": expires,
            "volume_limit_bytes": volume,
            "volume_used_bytes": 0,
            "panel": panel_info,
        }

    async def get_active(self, user_id: int) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT s.id, s.token, s.expires_at, s.volume_limit_bytes, s.volume_used_bytes,
                   pc.sub_id, pc.location_code, pc.subscription_id
            FROM subscriptions s
            LEFT JOIN panel_clients pc ON pc.subscription_id = s.id AND pc.is_active
            WHERE s.user_id=:user_id AND s.is_active AND s.expires_at > now()
            ORDER BY s.expires_at DESC LIMIT 1
            """,
            {"user_id": user_id},
        )
        return dict(row) if row else None

    async def subscription_url(self, token: uuid.UUID, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/sub/{token}"

    async def reset_daily_volumes(self) -> int:
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            result = await conn.execute(
                text(
                    """
                    UPDATE subscriptions
                    SET volume_used_bytes=0,
                        volume_limit_bytes=:base + (
                          SELECT referral_credit_bytes FROM users WHERE telegram_id=subscriptions.user_id
                        ),
                        expires_at=now() + interval '1 day'
                    WHERE is_active AND expires_at <= now() + interval '1 day'
                    RETURNING id
                    """
                ),
                {"base": DAILY_BASE_BYTES},
            )
            return len(result.fetchall())
