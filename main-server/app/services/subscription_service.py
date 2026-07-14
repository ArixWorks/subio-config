"""Subscription lifecycle, daily volume, and referral handling."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.db import Database
from app.services.panel_service import (
    DAILY_BASE_BYTES,
    PanelService,
    REFERRAL_BONUS_BYTES,
    next_tehran_midnight,
    tehran_service_day,
)


class SubscriptionService:
    def __init__(self, db: Database, panels: PanelService) -> None:
        self._db = db
        self._panels = panels

    async def ensure_user(self, telegram_id: int, username: str | None, language: str) -> None:
        await self._db.execute(
            """
            INSERT INTO users(telegram_id, username, language)
            VALUES (:id, :username, :language)
            ON CONFLICT (telegram_id) DO UPDATE
            SET username=excluded.username, language='fa'
            """,
            {"id": telegram_id, "username": username, "language": language},
        )

    async def register_referral(self, user_id: int, referrer_id: int) -> None:
        if user_id == referrer_id:
            return
        async with self._db.connection() as connection:
            from sqlalchemy import text

            result = await connection.execute(
                text(
                    """
                    UPDATE users
                    SET referred_by=:referrer
                    WHERE telegram_id=:id AND referred_by IS NULL
                      AND EXISTS (
                        SELECT 1 FROM users referrer WHERE referrer.telegram_id=:referrer
                      )
                    RETURNING telegram_id
                    """
                ),
                {"referrer": referrer_id, "id": user_id},
            )
            if result.first() is None:
                return
            await connection.execute(
                text(
                    """
                    UPDATE users
                    SET referral_credit_bytes=referral_credit_bytes+:bonus
                    WHERE telegram_id=:referrer
                    """
                ),
                {"referrer": referrer_id, "bonus": REFERRAL_BONUS_BYTES},
            )

    async def get_or_create_public_feed(self, user_id: int) -> dict[str, Any]:
        await self._db.execute(
            """
            INSERT INTO public_feeds(user_id)
            VALUES (:user_id)
            ON CONFLICT (user_id) DO UPDATE
            SET is_active=TRUE, updated_at=now()
            """,
            {"user_id": user_id},
        )
        row = await self._db.fetch_one(
            "SELECT token, created_at FROM public_feeds WHERE user_id=:user_id",
            {"user_id": user_id},
        )
        if row is None:
            raise RuntimeError("failed to create public feed")
        return row

    async def create_daily_subscription(
        self, user_id: int, location_code: str = "DE"
    ) -> dict[str, Any]:
        service_day = tehran_service_day()
        existing = await self._db.fetch_one(
            """
            SELECT s.id, s.token, s.expires_at, s.volume_limit_bytes, s.volume_used_bytes,
                   s.is_active,
                   s.location_code, pc.sub_id, p.base_url, p.name AS panel_name,
                   COALESCE(p.country_name_fa, p.name) AS country_name_fa
            FROM subscriptions s
            LEFT JOIN panel_clients pc ON pc.subscription_id=s.id AND pc.is_active
            LEFT JOIN panels p ON p.id=pc.panel_id
            WHERE s.user_id=:user_id AND s.service_day=:service_day
            ORDER BY s.created_at DESC LIMIT 1
            """,
            {"user_id": user_id, "service_day": service_day},
        )
        if existing:
            result = dict(existing)
            if result.get("is_active") and result.get("base_url") and result.get("sub_id"):
                result["subscription_url"] = (
                    f"{str(result['base_url']).rstrip('/')}/sub/{result['sub_id']}"
                )
                return result
            used = int(result["volume_used_bytes"])
            limit = int(result["volume_limit_bytes"])
            expires_at = result["expires_at"]
            if expires_at <= datetime.now(tz=UTC) or used >= limit:
                raise RuntimeError("daily private allocation is exhausted")
            if not result.get("is_active"):
                revoke_result = await self._panels.revoke_inactive_clients(user_id)
                if revoke_result["failed"]:
                    raise RuntimeError("previous private client could not be revoked")
            await self._db.execute(
                "UPDATE panel_clients SET is_active=FALSE WHERE subscription_id=:id",
                {"id": result["id"]},
            )
            await self._db.execute(
                """
                UPDATE subscriptions
                SET is_active=TRUE, location_code=:location_code
                WHERE id=:id
                """,
                {"id": result["id"], "location_code": location_code.upper()},
            )
            try:
                panel_info = await self._panels.create_private_client(
                    subscription_id=str(result["id"]),
                    user_id=user_id,
                    location_code=location_code,
                    expires_at=expires_at,
                    volume_bytes=max(1, limit - used),
                    usage_offset_bytes=used,
                )
            except Exception:
                await self._db.execute(
                    "UPDATE subscriptions SET is_active=FALSE WHERE id=:id",
                    {"id": result["id"]},
                )
                raise
            result.update(
                {
                    "location_code": location_code.upper(),
                    "country_name_fa": panel_info["country_name_fa"],
                    "panel_name": panel_info["panel"],
                    "subscription_url": panel_info["subscription_url"],
                }
            )
            return result
        user = await self._db.fetch_one(
            "SELECT referral_credit_bytes FROM users WHERE telegram_id=:id",
            {"id": user_id},
        )
        bonus = int(user["referral_credit_bytes"]) if user else 0
        volume = DAILY_BASE_BYTES + bonus
        token = uuid.uuid4()
        expires = next_tehran_midnight()
        sub_id = str(uuid.uuid4())
        await self._db.execute(
            """
            INSERT INTO subscriptions(
              id, token, user_id, volume_limit_bytes, expires_at, service_day, location_code
            )
            VALUES (
              :id, :token, :user_id, :volume, :expires, :service_day, :location_code
            )
            """,
            {
                "id": sub_id,
                "token": token,
                "user_id": user_id,
                "volume": volume,
                "expires": expires,
                "service_day": service_day,
                "location_code": location_code.upper(),
            },
        )
        try:
            panel_info = await self._panels.create_private_client(
                subscription_id=sub_id,
                user_id=user_id,
                location_code=location_code,
                expires_at=expires,
                volume_bytes=volume,
            )
        except Exception:
            await self._db.execute("DELETE FROM subscriptions WHERE id=:id", {"id": sub_id})
            raise
        if bonus:
            await self._db.execute(
                "UPDATE users SET referral_credit_bytes=0 WHERE telegram_id=:id",
                {"id": user_id},
            )
        return {
            "id": sub_id,
            "token": token,
            "expires_at": expires,
            "volume_limit_bytes": volume,
            "volume_used_bytes": 0,
            "location_code": location_code.upper(),
            "country_name_fa": panel_info["country_name_fa"],
            "panel_name": panel_info["panel"],
            "subscription_url": panel_info["subscription_url"],
        }

    async def get_active(self, user_id: int) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT s.id, s.token, s.expires_at, s.volume_limit_bytes, s.volume_used_bytes,
                   s.location_code, pc.sub_id, pc.subscription_id, p.base_url,
                   p.name AS panel_name, COALESCE(p.country_name_fa, p.name) AS country_name_fa
            FROM subscriptions s
            LEFT JOIN panel_clients pc ON pc.subscription_id = s.id AND pc.is_active
            LEFT JOIN panels p ON p.id=pc.panel_id
            WHERE s.user_id=:user_id AND s.is_active AND s.expires_at > now()
            ORDER BY s.expires_at DESC LIMIT 1
            """,
            {"user_id": user_id},
        )
        if row is None:
            return None
        result = dict(row)
        if result.get("base_url") and result.get("sub_id"):
            result["subscription_url"] = (
                f"{str(result['base_url']).rstrip('/')}/sub/{result['sub_id']}"
            )
        return result

    async def subscription_url(self, token: uuid.UUID, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/sub/{token}"

    async def reset_daily_volumes(self) -> int:
        async with self._db.connection() as connection:
            from sqlalchemy import text

            result = await connection.execute(
                text(
                    """
                    UPDATE subscriptions
                    SET is_active=FALSE
                    WHERE is_active AND expires_at <= now()
                    RETURNING id
                    """
                )
            )
            return len(result.fetchall())
