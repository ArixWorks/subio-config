"""AI-11: Multi-language UI helper with Redis cache."""

from __future__ import annotations

from app.ai.gateway import AIGateway

SYSTEM = """Translate SubIO bot UI strings. Keep product name SubIO.
Return JSON: {"text":"..."} Preserve placeholders like {url} {name}."""


async def translate_ui(
    gateway: AIGateway,
    *,
    text: str,
    target_lang: str,
    redis: object | None = None,
    cache_prefix: str = "ai:i18n",
) -> str:
    lang = (target_lang or "fa")[:5].lower()
    if lang.startswith("fa") or not text.strip():
        return text
    cache_key = f"{cache_prefix}:{lang}:{hash(text)}"
    if redis is not None:
        cached = await redis.get(cache_key)  # type: ignore[attr-defined]
        if cached:
            return str(cached)
    if not gateway.enabled:
        return text
    result = await gateway.chat_json(
        system=SYSTEM,
        user=f"target_lang={lang}\ntext:\n{text}",
        tier="luna",
        max_tokens=400,
        default={"text": text},
    )
    out = text
    if isinstance(result, dict):
        candidate = str(result.get("text") or "").strip()
        if candidate:
            out = candidate
    if redis is not None:
        await redis.set(cache_key, out, ex=7 * 24 * 3600)  # type: ignore[attr-defined]
    return out
