"""AI-2: Extract VPN URIs from messy / Persian-broken text."""

from __future__ import annotations

import re

from app.ai.gateway import AIGateway
from app.services.decrypt_service import extract_plain_configs

SYSTEM = """You extract VPN share links from messy Persian/English Telegram text.
Return JSON: {"uris":["vless://...","vmess://..."]}
Only include real share URIs (vless, vmess, trojan, ss, hysteria2, wireguard).
Repair broken line-wraps and zero-width spaces. Never invent host/uuid — only repair formatting.
If none, return {"uris":[]}."""

_URI_HINT = re.compile(r"(vless|vmess|trojan|ss|hy2|hysteria2|wireguard)://", re.I)


async def extract_uris_ai(gateway: AIGateway, text: str) -> set[str]:
    found = extract_plain_configs(text)
    if found or not text.strip():
        return found
    if not _URI_HINT.search(text) and "وی‌لس" not in text and "vmess" not in text.lower():
        return found
    if not gateway.enabled:
        return found
    result = await gateway.chat_json(
        system=SYSTEM,
        user=text[:3500],
        tier="sol",
        max_tokens=1200,
        default={"uris": []},
    )
    if not isinstance(result, dict):
        return found
    uris = result.get("uris") or []
    out = set(found)
    for item in uris:
        if isinstance(item, str) and "://" in item and len(item) > 12:
            out.add(item.strip())
    return out
