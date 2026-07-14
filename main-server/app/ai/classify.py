"""AI-1: Classify scanned messages/files as config vs spam."""

from __future__ import annotations

from typing import Any

from app.ai.gateway import AIGateway

SYSTEM = """You classify Telegram messages for a VPN config collector (Iran).
Return JSON:
{"relevant":true/false,"kind":"config|encrypted_config|spam|noise|ops|other","confidence":0-1,"reason":"short"}
relevant=true only if the message likely contains VPN share links, encrypted VPN payloads, NPV text, or decryptable config files.
ops = outage/operator reports without configs.
spam = ads, giveaway, unrelated."""


async def classify_message(
    gateway: AIGateway,
    *,
    text: str,
    filename: str | None = None,
) -> dict[str, Any]:
    snippet = (text or "")[:2500]
    if not gateway.enabled:
        has_scheme = any(
            token in snippet.lower()
            for token in ("vless://", "vmess://", "trojan://", "ss://", "slipnet-enc://", "nm-")
        )
        return {
            "relevant": bool(has_scheme or filename),
            "kind": "config" if has_scheme else ("encrypted_config" if filename else "noise"),
            "confidence": 0.5,
            "reason": "heuristic_fallback",
        }
    result = await gateway.chat_json(
        system=SYSTEM,
        user=f"filename={filename or '-'}\ntext:\n{snippet}",
        tier="luna",
        default={"relevant": True, "kind": "other", "confidence": 0.4, "reason": "fallback"},
    )
    if not isinstance(result, dict):
        return {"relevant": True, "kind": "other", "confidence": 0.4, "reason": "bad_json"}
    return {
        "relevant": bool(result.get("relevant", True)),
        "kind": str(result.get("kind") or "other"),
        "confidence": float(result.get("confidence") or 0.5),
        "reason": str(result.get("reason") or ""),
    }
