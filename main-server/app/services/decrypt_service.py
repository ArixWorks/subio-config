"""VPNDecryptorBot relay: wait for .json reply, convert convertible profiles only."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.custom.message import Message

from app.services.decryptor_json import decryptor_json_to_uris

logger = logging.getLogger("subio.decrypt")

DECRYPTOR_BOT = "VPNDecryptorBot"
DECRYPT_TIMEOUT = 90.0
DECRYPT_COOLDOWN = 2.5
MAX_RESPONSES = 8

DECRYPT_EXTENSIONS = {".ehi", ".npvt", ".npvtsub", ".hat", ".hc"}
ENCRYPTED_SCHEME_RE = re.compile(
    r"(?:slipnet-enc://|nm-[a-z0-9]+://)[^\s<>'\"]+",
    re.IGNORECASE,
)
ENCRYPTED_MARKERS = (
    "sub://",
    "happ://",
    "hiddify://",
    "slipnet-enc://",
    "encrypted",
    "🔒",
    "رمز",
)

CONFIG_PATTERN = re.compile(
    r"(?:vless|vmess|trojan|ss|wireguard)://[^\s<>'\"]+",
    re.IGNORECASE,
)

_last_decrypt_at = 0.0


def extract_plain_configs(text: str) -> set[str]:
    return {match.rstrip(").,،") for match in CONFIG_PATTERN.findall(text or "")}


def extract_encrypted_schemes(text: str) -> set[str]:
    return {match.rstrip(").,،") for match in ENCRYPTED_SCHEME_RE.findall(text or "")}


def looks_encrypted(text: str) -> bool:
    lowered = (text or "").lower()
    if any(marker in lowered for marker in ENCRYPTED_MARKERS):
        return True
    if extract_encrypted_schemes(text):
        return True
    return bool(re.search(r"nm-[a-z0-9]+://", lowered))


# Alias kept for callers/tests that parse a decryptor .json payload directly
# through this module rather than importing decryptor_json_to_uris themselves.
_configs_from_json = decryptor_json_to_uris


def document_needs_decrypt(filename: str | None) -> bool:
    if not filename:
        return False
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in DECRYPT_EXTENSIONS)


def _is_json_document(message: Message) -> bool:
    if not message.document or not message.file:
        return False
    name = str(message.file.name or "").lower()
    mime = str(getattr(message.document, "mime_type", "") or "").lower()
    return name.endswith(".json") or mime in {"application/json", "text/json"}


async def _rate_limit_decrypt() -> None:
    global _last_decrypt_at
    elapsed = asyncio.get_running_loop().time() - _last_decrypt_at
    if elapsed < DECRYPT_COOLDOWN:
        await asyncio.sleep(DECRYPT_COOLDOWN - elapsed)
    _last_decrypt_at = asyncio.get_running_loop().time()


async def _wait_for_json_document(conv: Any) -> Message | None:
    """Wait for the first bot reply that carries a .json file (any order)."""
    for index in range(1, MAX_RESPONSES + 1):
        try:
            msg: Message = await conv.get_response(timeout=DECRYPT_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("decrypt_wait_timeout", extra={"seen": index - 1})
            return None
        if _is_json_document(msg):
            logger.info("decrypt_json_message_found", extra={"message_index": index})
            return msg
        logger.debug(
            "decrypt_skip_non_json",
            extra={
                "message_index": index,
                "has_document": bool(msg.document),
                "filename": getattr(msg.file, "name", None) if msg.file else None,
            },
        )
    logger.warning("decrypt_json_not_found_after_max_messages")
    return None


async def _uris_from_json_message(client: TelegramClient, message: Message) -> set[str]:
    """Parse only convertible configs from a decryptor .json document."""
    found: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="subio-dec-") as directory:
        filename = (message.file.name if message.file else None) or "decrypted.json"
        path = Path(directory) / filename
        await client.download_media(message, file=str(path))
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            logger.warning("decrypt_json_empty")
            return found
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("decrypt_json_invalid")
            return found
        found = decryptor_json_to_uris(payload)
        if not found:
            logger.info("decrypt_json_no_convertible_configs")
    return found


async def decrypt_payload_via_bot(
    client: TelegramClient,
    *,
    text: str | None = None,
    file_path: Path | None = None,
    caption: str | None = None,
) -> list[str]:
    await _rate_limit_decrypt()
    bot = await client.get_entity(DECRYPTOR_BOT)
    try:
        async with client.conversation(bot, timeout=int(DECRYPT_TIMEOUT)) as conv:
            if file_path is not None:
                await conv.send_file(str(file_path), caption=caption or "")
            elif text:
                await conv.send_message(text.strip())
            else:
                return []
            json_message = await _wait_for_json_document(conv)
    except FloodWaitError as exc:
        logger.warning("decrypt_flood_wait", extra={"seconds": exc.seconds})
        await asyncio.sleep(exc.seconds)
        return await decrypt_payload_via_bot(
            client, text=text, file_path=file_path, caption=caption
        )
    except Exception:
        logger.exception("decrypt_conversation_failed")
        return []

    if json_message is None:
        return []

    uris = await _uris_from_json_message(client, json_message)
    result = sorted(uris)
    logger.info("decrypt_result", extra={"configs": len(result)})
    return result


async def decrypt_via_bot(client: TelegramClient, text: str) -> list[str]:
    return await decrypt_payload_via_bot(client, text=text)


async def decrypt_file_via_bot(client: TelegramClient, path: Path, filename: str) -> list[str]:
    return await decrypt_payload_via_bot(client, file_path=path, caption=filename)
