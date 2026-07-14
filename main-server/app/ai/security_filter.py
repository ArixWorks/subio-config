"""AI-10: Suspicious config security filter."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.ai.gateway import AIGateway

BLOCKED_HOST_HINTS = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "metadata.google",
    "169.254.",
)

SYSTEM = """You are a security filter for VPN share URIs before Iran testing.
Return JSON: {"safe":true/false,"risk":"none|low|medium|high","reasons":["..."]}
Flag phishing-like hosts, private IPs, overly suspicious SNI/host mismatches when clear.
If unsure but looks like normal VPN, safe=true with risk=low."""


def _heuristic(uri: str) -> dict[str, Any]:
    host = (urlsplit(uri).hostname or "").lower()
    if not host:
        return {"safe": False, "risk": "high", "reasons": ["missing_host"]}
    if any(h in host for h in BLOCKED_HOST_HINTS):
        return {"safe": False, "risk": "high", "reasons": ["local_or_metadata_host"]}
    if host.endswith(".local") or host.endswith(".internal"):
        return {"safe": False, "risk": "high", "reasons": ["internal_tld"]}
    return {"safe": True, "risk": "none", "reasons": []}


async def security_filter(gateway: AIGateway, uri: str) -> dict[str, Any]:
    base = _heuristic(uri)
    if not base["safe"] or not gateway.enabled:
        return base
    # Only ask AI for edge cases; keep traffic cost low — skip if clearly normal public host.
    host = urlsplit(uri).hostname or ""
    if host.count(".") >= 1 and not any(ch.isdigit() for ch in host.split(".")[0]):
        # Still sample AI for non-ascii / IP literals
        if all(ord(c) < 128 for c in uri[:200]) and not host.replace(".", "").isdigit():
            return base
    result = await gateway.chat_json(
        system=SYSTEM,
        user=uri[:1200],
        tier="luna",
        max_tokens=200,
        default=base,
    )
    if not isinstance(result, dict):
        return base
    return {
        "safe": bool(result.get("safe", True)),
        "risk": str(result.get("risk") or "low"),
        "reasons": [str(x) for x in (result.get("reasons") or [])][:5],
    }
