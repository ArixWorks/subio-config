"""AI-3: Clean config naming suggestions."""

from __future__ import annotations

from typing import Any

from app.ai.gateway import AIGateway
from app.services.scoring_service import format_config_name

SYSTEM = """You name VPN configs for SubIO brand.
Return JSON: {"display_name":"🇩🇪 SubIO Reality | پایدار"}
Rules: short Persian/English mix ok, include country flag emoji, protocol/transport hint, quality word.
Max 48 chars. Never invent fake country if unknown — use 🌍."""


async def suggest_display_name(
    gateway: AIGateway,
    *,
    protocol: str,
    transport: str,
    location: str,
    latency_ms: int | None,
    score: float,
    config_id: int,
) -> str:
    fallback = format_config_name(
        config_id=config_id,
        location=location,
        protocol=protocol,
        latency_ms=latency_ms,
        score=score,
        transport=transport,
    )
    if not gateway.enabled:
        return fallback
    result = await gateway.chat_json(
        system=SYSTEM,
        user=(
            f"protocol={protocol} transport={transport} location={location} "
            f"latency_ms={latency_ms} score={score}"
        ),
        tier="luna",
        max_tokens=120,
        default={"display_name": fallback},
    )
    if not isinstance(result, dict):
        return fallback
    name = str(result.get("display_name") or "").strip()
    if 4 <= len(name) <= 64:
        return name
    return fallback


async def naming_context(**kwargs: Any) -> str:
    return str(kwargs)
