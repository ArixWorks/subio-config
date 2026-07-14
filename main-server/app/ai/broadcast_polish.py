"""AI-7: Polish broadcast / invite copy before send."""

from __future__ import annotations

from app.ai.gateway import AIGateway

SYSTEM = """Improve Telegram broadcast copy for SubIO VPN users in Iran.
Return JSON: {"message":"..."}
Keep meaning, shorten fluff, polite Persian, max 3500 chars, keep links/tokens unchanged.
Do not add fake discounts or claims."""


async def polish_broadcast(gateway: AIGateway, message: str) -> str:
    original = message.strip()
    if not original or not gateway.enabled:
        return original
    result = await gateway.chat_json(
        system=SYSTEM,
        user=original,
        tier="luna",
        max_tokens=900,
        default={"message": original},
    )
    if not isinstance(result, dict):
        return original
    polished = str(result.get("message") or "").strip()
    if 1 <= len(polished) <= 4096:
        return polished
    return original
