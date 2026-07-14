"""Local NPV / subscription blob to v2ray URI conversion."""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any
from urllib.parse import unquote

from app.services.decrypt_service import CONFIG_PATTERN, extract_plain_configs

logger = logging.getLogger("subio.npv")

NPV_MARKERS = (
    "npv://",
    "npvt://",
    "npv4://",
    "napsternetv://",
    "nvp://",
)

BASE64_CHUNK = re.compile(r"[A-Za-z0-9+/=_-]{40,}")


def looks_like_npv(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in NPV_MARKERS)


def _decode_base64_padded(raw: str) -> str | None:
    for candidate in (raw, raw + "=", raw + "=="):
        try:
            return base64.b64decode(candidate, validate=False).decode("utf-8", errors="replace")
        except (ValueError, UnicodeDecodeError):
            try:
                return base64.urlsafe_b64decode(candidate + "==").decode("utf-8", errors="replace")
            except (ValueError, UnicodeDecodeError):
                continue
    return None


def _vmess_json_to_uri(data: dict[str, Any]) -> str | None:
    if not data.get("id") or not data.get("add"):
        return None
    payload = {
        "v": "2",
        "ps": data.get("ps", "npv"),
        "add": data["add"],
        "port": str(data.get("port", 443)),
        "id": data["id"],
        "aid": str(data.get("aid", 0)),
        "net": data.get("net", "tcp"),
        "type": data.get("type", "none"),
        "host": data.get("host", ""),
        "path": data.get("path", ""),
        "tls": data.get("tls", ""),
        "sni": data.get("sni", data.get("host", "")),
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return f"vmess://{encoded.rstrip('=')}"


def _objects_to_uris(payload: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, str):
        found.update(extract_plain_configs(payload))
        decoded = _decode_base64_padded(payload.strip())
        if decoded:
            found.update(extract_plain_configs(decoded))
            try:
                return found | _objects_to_uris(json.loads(decoded))
            except json.JSONDecodeError:
                pass
        return found
    if isinstance(payload, dict):
        if payload.get("add") and payload.get("id"):
            uri = _vmess_json_to_uri(payload)
            if uri:
                found.add(uri)
        for value in payload.values():
            found.update(_objects_to_uris(value))
    elif isinstance(payload, list):
        for item in payload:
            found.update(_objects_to_uris(item))
    return found


def convert_npv_to_v2ray(text: str) -> set[str]:
    """Best-effort local conversion without external bots."""
    uris: set[str] = set()
    uris.update(extract_plain_configs(text))

    for match in BASE64_CHUNK.findall(text):
        decoded = _decode_base64_padded(match)
        if not decoded:
            continue
        uris.update(extract_plain_configs(decoded))
        try:
            uris.update(_objects_to_uris(json.loads(decoded)))
        except json.JSONDecodeError:
            for line in decoded.replace("\r", "\n").split("\n"):
                line = line.strip()
                if not line:
                    continue
                inner = _decode_base64_padded(line)
                if inner:
                    uris.update(extract_plain_configs(inner))
                    try:
                        uris.update(_objects_to_uris(json.loads(inner)))
                    except json.JSONDecodeError:
                        pass

    for marker in NPV_MARKERS:
        if marker in text.lower():
            fragment = text.lower().split(marker, 1)[-1].split()[0]
            decoded = _decode_base64_padded(unquote(fragment))
            if decoded:
                uris.update(_objects_to_uris(decoded))
                uris.update(extract_plain_configs(decoded))

    logger.info("npv_converted", extra={"input_len": len(text), "configs": len(uris)})
    return uris
