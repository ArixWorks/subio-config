"""Extract configs from channel/group/bot messages with scanner settings + AI."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.custom.message import Message

from app.ai.classify import classify_message
from app.ai.extract import extract_uris_ai
from app.ai.gateway import get_gateway
from app.services.decrypt_service import (
    decrypt_file_via_bot,
    decrypt_via_bot,
    document_needs_decrypt,
    extract_encrypted_schemes,
    extract_plain_configs,
    looks_encrypted,
)
from app.services.npv_converter import convert_npv_to_v2ray, looks_like_npv
from app.services.scanner_settings_service import ScannerSettings

logger = logging.getLogger("subio.scanner")


async def extract_configs_from_event(
    client: TelegramClient,
    message: Message,
    settings: ScannerSettings,
) -> set[str]:
    text = message.raw_text or message.message or ""
    filename = None
    if message.document and message.file:
        filename = str(message.file.name or "")

    gateway = get_gateway()
    verdict = await classify_message(gateway, text=text, filename=filename)
    if not verdict.get("relevant", True) and verdict.get("confidence", 0) >= 0.7:
        logger.info(
            "ai_skip_irrelevant",
            extra={"kind": verdict.get("kind"), "reason": verdict.get("reason")},
        )
        return set()

    uris: set[str] = set()

    # 1) Plain share links always collected.
    uris.update(extract_plain_configs(text))

    # 1b) AI extraction when regex finds nothing but text looks related.
    if not uris:
        uris.update(await extract_uris_ai(gateway, text))

    # 2) Local NPV conversion when enabled.
    if settings.npv_to_v2ray and looks_like_npv(text):
        uris.update(convert_npv_to_v2ray(text))

    # 3) Encrypted schemes → decryptor bot.
    schemes = extract_encrypted_schemes(text)
    if settings.decrypt_bot and (schemes or looks_encrypted(text)):
        payload = "\n".join(sorted(schemes)) if schemes else text
        if payload.strip():
            try:
                uris.update(await decrypt_via_bot(client, payload))
            except Exception:
                logger.exception("decrypt_text_failed")

    # 4) Supported locked files → decryptor bot (JSON reply with profiles).
    if settings.decrypt_bot and message.document and message.file:
        if document_needs_decrypt(filename or ""):
            try:
                with tempfile.TemporaryDirectory(prefix="subio-enc-") as directory:
                    path = Path(directory) / (filename or "payload.bin")
                    await client.download_media(message, file=str(path))
                    uris.update(await decrypt_file_via_bot(client, path, filename or "payload.bin"))
            except Exception:
                logger.exception("decrypt_file_failed", extra={"file": filename})

    return settings.filter_uris(uris)
