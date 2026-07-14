"""AI-12: Scanner settings recommendations."""

from __future__ import annotations

from typing import Any

from app.ai.gateway import AIGateway

SYSTEM = """You advise SubIO scanner settings (npv_to_v2ray, decrypt_bot, protocols).
Return JSON:
{"summary":"fa","actions":[{"setting":"decrypt_bot","value":false,"reason":"..."}],"priority":"low|medium|high"}
Prefer disabling decrypt_bot if decrypt failures dominate. Prefer narrowing protocols if many unsupported."""


async def recommend_scanner_settings(
    gateway: AIGateway,
    *,
    current: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    default = {
        "summary": "تغییری پیشنهاد نشد",
        "actions": [],
        "priority": "low",
    }
    decrypt_fail = int(metrics.get("decrypt_failures", 0) or 0)
    decrypt_ok = int(metrics.get("decrypt_successes", 0) or 0)
    if current.get("decrypt_bot") and decrypt_fail > 20 and decrypt_fail > decrypt_ok * 3:
        default = {
            "summary": "نرخ شکست decrypt بالا است؛ پیشنهاد: خاموش کردن ربات decrypt.",
            "actions": [
                {
                    "setting": "decrypt_bot",
                    "value": False,
                    "reason": "decrypt_fail_ratio_high",
                }
            ],
            "priority": "high",
        }
    if not gateway.enabled:
        return default
    result = await gateway.chat_json(
        system=SYSTEM,
        user=f"current={current}\nmetrics={metrics}",
        tier="sol",
        max_tokens=500,
        default=default,
    )
    if not isinstance(result, dict):
        return default
    actions = result.get("actions") or []
    if not isinstance(actions, list):
        actions = []
    priority = str(result.get("priority") or "low")
    if priority not in {"low", "medium", "high"}:
        priority = "low"
    return {
        "summary": str(result.get("summary") or default["summary"]),
        "actions": actions[:8],
        "priority": priority,
    }
