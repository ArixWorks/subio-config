"""Broadcast message delivery via aiogram with rate limiting."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from app.ai.broadcast_polish import polish_broadcast
from app.ai.gateway import get_gateway
from app.db import Database

logger = logging.getLogger("subio.broadcast")

SEND_DELAY = 0.05  # ~20 msg/s
BATCH_SIZE = 500


class BroadcastService:
    def __init__(self, db: Database, bot_token: str) -> None:
        self._db = db
        self._bot_token = bot_token

    async def process_pending(self, limit: int = 1) -> int:
        processed = 0
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT id, message, target FROM broadcasts
                        WHERE status='pending'
                        ORDER BY created_at ASC LIMIT :limit
                        """
                    ),
                    {"limit": limit},
                )
            ).mappings().all()

        for row in rows:
            await self._run_broadcast(str(row["id"]), str(row["message"]), str(row["target"]))
            processed += 1
        return processed

    async def run_broadcast(self, broadcast_id: str) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT id, message, target, status FROM broadcasts WHERE id=:id",
            {"id": broadcast_id},
        )
        if not row:
            raise ValueError("broadcast not found")
        if str(row["status"]) != "pending":
            return {"status": str(row["status"]), "skipped": True}
        return await self._run_broadcast(str(row["id"]), str(row["message"]), str(row["target"]))

    async def _run_broadcast(self, broadcast_id: str, message: str, target: str) -> dict[str, Any]:
        await self._db.execute(
            "UPDATE broadcasts SET status='running' WHERE id=:id AND status='pending'",
            {"id": broadcast_id},
        )
        polished = await polish_broadcast(get_gateway(), message)
        if polished != message:
            await self._db.execute(
                "UPDATE broadcasts SET message=:message WHERE id=:id",
                {"id": broadcast_id, "message": polished},
            )
            message = polished
        recipients = await self._recipients(target)
        sent = 0
        failed = 0
        bot = Bot(self._bot_token)
        try:
            for user_id in recipients:
                try:
                    await bot.send_message(user_id, message)
                    sent += 1
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after)
                    try:
                        await bot.send_message(user_id, message)
                        sent += 1
                    except Exception:
                        failed += 1
                except TelegramForbiddenError:
                    failed += 1
                    await self._db.execute(
                        "UPDATE users SET is_blocked=TRUE WHERE telegram_id=:id",
                        {"id": user_id},
                    )
                except Exception:
                    logger.exception("broadcast_send_failed", extra={"user_id": user_id})
                    failed += 1
                await asyncio.sleep(SEND_DELAY)
        finally:
            await bot.session.close()

        status = "completed" if failed == 0 else "completed_with_errors"
        await self._db.execute(
            """
            UPDATE broadcasts
            SET status=:status, sent_count=:sent, failed_count=:failed, completed_at=now()
            WHERE id=:id
            """,
            {"id": broadcast_id, "status": status, "sent": sent, "failed": failed},
        )
        logger.info("broadcast_done", extra={"id": broadcast_id, "sent": sent, "failed": failed})
        return {"id": broadcast_id, "sent": sent, "failed": failed, "status": status}

    async def _recipients(self, target: str) -> list[int]:
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            if target == "active":
                rows = (
                    await conn.execute(
                        text(
                            """
                            SELECT DISTINCT u.telegram_id
                            FROM users u
                            JOIN subscriptions s ON s.user_id = u.telegram_id
                            WHERE u.is_blocked=FALSE AND s.is_active AND s.expires_at > now()
                            LIMIT :limit
                            """
                        ),
                        {"limit": BATCH_SIZE},
                    )
                ).fetchall()
            else:
                rows = (
                    await conn.execute(
                        text(
                            """
                            SELECT telegram_id FROM users
                            WHERE is_blocked=FALSE
                            LIMIT :limit
                            """
                        ),
                        {"limit": BATCH_SIZE},
                    )
                ).fetchall()
        return [int(row[0]) for row in rows]
