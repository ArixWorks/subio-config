"""Monitor all non-private chats for VPN configs and queue them for Iran testing."""

from __future__ import annotations

import hashlib
import logging
from urllib.parse import urlsplit

from telethon import events
from telethon.tl.types import User

from app.ai.gateway import get_gateway
from app.ai.security_filter import security_filter
from app.config import get_settings
from app.db import Database
from app.logging import configure_logging
from app.security import PayloadCipher
from app.services.pipeline_events import PipelineEventService
from app.services.scanner_pipeline import extract_configs_from_event
from app.services.scanner_settings_service import ScannerSettingsService
from app.telethon_utils import create_client
from redis.asyncio import Redis

logger = logging.getLogger("subio.monitor")


async def store_configs(
    db: Database,
    cipher: PayloadCipher,
    uris: set[str],
    source: str,
    events: PipelineEventService,
) -> int:
    stored = 0
    gateway = get_gateway()
    for uri in uris:
        try:
            verdict = await security_filter(gateway, uri)
        except Exception:
            logger.exception("security_filter_failed")
            verdict = {"safe": True, "risk": "unknown", "reasons": []}
        if not verdict.get("safe", True):
            logger.warning(
                "config_blocked_security",
                extra={"source": source, "risk": verdict.get("risk"), "reasons": verdict.get("reasons")},
            )
            continue
        protocol = urlsplit(uri).scheme.lower() or "unknown"
        fingerprint = hashlib.sha256(uri.encode()).hexdigest()
        encrypted = cipher.encrypt({"uri": uri}, aad=b"subio:config:v1")
        async with db.connection() as conn:
            from sqlalchemy import text as sql_text

            row = (
                await conn.execute(
                    sql_text(
                        """
                        INSERT INTO vpn_configs(scope, protocol, fingerprint, uri_enc, score, source_chat)
                        VALUES ('public', :protocol, :fingerprint, :uri_enc, 25, :source)
                        ON CONFLICT (fingerprint) DO NOTHING
                        RETURNING id::text AS id, config_code
                        """
                    ),
                    {
                        "protocol": protocol,
                        "fingerprint": fingerprint,
                        "uri_enc": encrypted,
                        "source": source,
                    },
                )
            ).mappings().first()
        stored += 1
        if row is not None:
            await events.emit(
                stage="ingested",
                status="info",
                config_id=str(row["id"]),
                config_code=row.get("config_code"),
                message=f"کانفیگ جدید از {source} دریافت شد",
                metadata={"protocol": protocol, "source": source},
            )
    return stored


def _source_name(chat: object, chat_id: int) -> str:
    username = getattr(chat, "username", None)
    title = getattr(chat, "title", None)
    if username:
        return f"@{username}"
    if title:
        return str(title)
    return str(chat_id)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not all((settings.telethon_api_id, settings.telethon_api_hash, settings.telethon_session)):
        raise RuntimeError("Telethon monitoring variables are incomplete")

    db = Database(settings.database_url)
    cache = Redis.from_url(settings.redis_url, decode_responses=True)
    scanner_settings = ScannerSettingsService(db, cache)
    cipher = PayloadCipher(settings.payload_encryption_key)
    pipeline_events = PipelineEventService(db)
    client = create_client(settings)
    allowlist = set(settings.source_chats)

    @client.on(events.NewMessage)
    async def on_message(event: events.NewMessage.Event) -> None:
        # Only groups / channels / bots in groups-channels — never private DMs.
        if event.is_private:
            sender = await event.get_sender()
            # Ignore user PMs; allow messages from bots only if not private? User asked
            # exclude private — so skip all private chats entirely.
            if isinstance(sender, User):
                return
            return

        if allowlist:
            # Optional restriction: if TELETHON_SOURCE_CHATS is set, only those chats.
            chat_id = event.chat_id
            usernames = set()
            chat = await event.get_chat()
            if getattr(chat, "username", None):
                usernames.add(f"@{chat.username}")
                usernames.add(chat.username)
            keys = {str(chat_id), *usernames}
            if keys.isdisjoint(allowlist):
                return
        else:
            chat = await event.get_chat()

        source = _source_name(chat, int(event.chat_id))
        scan_settings = await scanner_settings.get()
        if not scan_settings.protocols:
            return

        try:
            uris = await extract_configs_from_event(client, event.message, scan_settings)
        except Exception:
            logger.exception("extract_failed", extra={"source": source})
            return

        if not uris:
            return
        count = await store_configs(db, cipher, uris, source, pipeline_events)
        logger.info(
            "configs_stored",
            extra={"source": source, "count": count, "uris": len(uris)},
        )

    try:
        await client.start()
        logger.info(
            "monitor_started",
            extra={"mode": "allowlist" if allowlist else "all_non_private"},
        )
        await client.run_until_disconnected()
    finally:
        await client.disconnect()
        await cache.aclose()
        await db.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
