"""Shared Telethon client factory."""

from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import Settings


def create_client(settings: Settings) -> TelegramClient:
    if not all(
        (
            settings.telethon_api_id,
            settings.telethon_api_hash,
            settings.telethon_session,
        )
    ):
        raise RuntimeError("Telethon credentials are incomplete")
    return TelegramClient(
        StringSession(settings.telethon_session),
        settings.telethon_api_id,
        settings.telethon_api_hash,
    )
