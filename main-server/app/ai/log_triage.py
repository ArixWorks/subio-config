"""AI-9: Auto-triage application / worker logs."""

from __future__ import annotations

from typing import Any

from app.ai.gateway import AIGateway

SYSTEM = """You triage SubIO VPN platform logs (main abroad + Iran tester).
Return JSON:
{"severity":"short","severity":"low|medium|high|critical","likely_cause":"...","actions":["..."],"component":"tester|scanner|bot|db|s3|other"}"""


async def triage_logs(gateway: AIGateway, logs: str) -> dict[str, Any]:
    default = {
        "severity": "لاگ بدون نتیجه AI",
        "severity": "low",
        "likely_cause": "unknown",
        "actions": ["بررسی دستی لاگ‌ها"],
        "component": "other",
    }
    if not logs.strip():
        return default
    if not gateway.enabled:
        return default
    result = await gateway.chat_json(
        system=SYSTEM,
        user=logs[:6000],
        tier="sol",
        max_tokens=600,
        default=default,
    )
    if not isinstance(result, dict):
        return default
    severity = str(result.get("severity") or "low").lower()
    if severity not in {"low", "medium", "high", "critical"}:
        severity = "low"
    actions = result.get("actions") or []
    if not isinstance(actions, list):
        actions = [str(actions)]
    return {
        "headline": str(result.get("headline") or default["headline"]),
        "severity": severity,
        "likely_cause": str(result.get("likely_cause") or ""),
        "actions": [str(a) for a in actions][:6],
        "component": str(result.get("component") or "other"),
    }
