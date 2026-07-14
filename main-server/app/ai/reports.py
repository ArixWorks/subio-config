"""AI-4: Infer mobile operator / outage category from Persian reports."""

from __future__ import annotations

from typing import Any

from app.ai.gateway import AIGateway

SYSTEM = """You analyze Iranian VPN outage reports in Persian/English.
Return JSON:
{"operator":"mci|irancell|rightel|unknown","category":"blocked|slow|disconnect|other","confidence":0-1,"summary":"short fa"}
operator synonyms: همراه اول/همراه=mci, ایرانسل=irancell, رایتل=rightel."""


async def infer_operator_report(gateway: AIGateway, text: str) -> dict[str, Any]:
    default = {
        "operator": "unknown",
        "category": "other",
        "confidence": 0.3,
        "summary": text[:120],
    }
    if not text.strip():
        return default
    lower = text.lower()
    if any(x in text for x in ("همراه", "مسی", "mci")) or "mci" in lower:
        default["operator"] = "mci"
    elif any(x in text for x in ("ایرانسل", "irancell")) or "mtn" in lower:
        default["operator"] = "irancell"
    elif "رایتل" in text or "rightel" in lower:
        default["operator"] = "rightel"
    if any(x in text for x in ("مسدود", "فیلتر", "باز نمیشه")):
        default["category"] = "blocked"
    elif any(x in text for x in ("کند", "لاگ", "کندی")):
        default["category"] = "slow"
    elif any(x in text for x in ("قطع", "دراپ", "disconnect")):
        default["category"] = "disconnect"
    if not gateway.enabled:
        return default
    result = await gateway.chat_json(
        system=SYSTEM,
        user=text[:1500],
        tier="luna",
        default=default,
    )
    if not isinstance(result, dict):
        return default
    operator = str(result.get("operator") or "unknown").lower()
    if operator not in {"mci", "irancell", "rightel", "unknown"}:
        operator = "unknown"
    category = str(result.get("category") or "other").lower()
    if category not in {"blocked", "slow", "disconnect", "other"}:
        category = "other"
    return {
        "operator": operator,
        "category": category,
        "confidence": float(result.get("confidence") or 0.5),
        "summary": str(result.get("summary") or text[:120]),
    }
