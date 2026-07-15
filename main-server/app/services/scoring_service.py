"""Health score computation and operator-aware adjustments."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from app.services.panel_service import country_flag

BOT_DISPLAY_HANDLE = "@Config_SubBOT"
TOP_SCORE_STAR_THRESHOLD = 85.0

SITE_WEIGHTS = {
    "instagram": 15,
    "youtube": 15,
    "cloudflare": 10,
    "telegram": 20,
    "http": 10,
}

OPERATOR_PENALTIES = {
    "mci": {"blocked": 8, "slow": 4},
    "irancell": {"blocked": 8, "slow": 4},
    "rightel": {"blocked": 6, "slow": 3},
    "unknown": {"blocked": 5, "slow": 2},
}


def compute_health_score(result: dict[str, Any]) -> float:
    if not result.get("reachable"):
        return 0.0
    score = 40.0
    latency = int(result.get("latency_ms") or 999)
    score += max(0, 25 - latency / 4)
    checks = result.get("checks") or {}
    for site, weight in SITE_WEIGHTS.items():
        if checks.get(site):
            score += weight
    speed = result.get("download_mbps")
    if speed is not None:
        score += min(15, float(speed) * 3)
    if result.get("mode") == "cheap" and score < 55:
        score = max(score, 55.0)
    return round(min(100, max(0, score)), 2)


def apply_operator_reports(
    base_score: float,
    operator: str,
    report_counts: dict[str, int],
) -> float:
    penalties = OPERATOR_PENALTIES.get(operator, OPERATOR_PENALTIES["unknown"])
    score = base_score
    score -= report_counts.get("blocked", 0) * penalties["blocked"]
    score -= report_counts.get("slow", 0) * penalties["slow"]
    score -= report_counts.get("disconnect", 0) * 3
    return round(max(0, min(100, score)), 2)


def format_config_name(
    *,
    config_code: str,
    country_code: str,
    score: float,
) -> str:
    """Renders the public, user-facing config label.

    Format: "{flag}[⭐] {BOT_DISPLAY_HANDLE} #{COUNTRY}{CODE}", e.g.
    "🇮🇹 @Config_SubBOT #IT454" or "🇮🇹⭐ @Config_SubBOT #IT454" once the
    config's score crosses TOP_SCORE_STAR_THRESHOLD. config_code is the
    stable numeric suffix allocated once per row (see migration 006); the
    country prefix is recomputed on every naming pass so a later, more
    accurate GeoIP resolution is reflected without changing the reportable
    code itself.
    """
    country = country_code.upper() if len(country_code) == 2 else "XX"
    flag = country_flag(country)
    star = "⭐" if score >= TOP_SCORE_STAR_THRESHOLD else ""
    code = f"{country}{config_code}"
    return f"{flag}{star} {BOT_DISPLAY_HANDLE} #{code}"


def with_display_name(uri: str, display_name: str) -> str:
    """Overwrites a config URI's fragment (the "#remark" every VPN client
    renders as the entry's visible name) with the SubIO display name.

    Client apps (v2rayNG, NekoBox, Shadowrocket, ...) all read the URI
    fragment as the human-facing label, so this is what actually surfaces
    "🇮🇹 @Config_SubBOT #IT454" inside the user's VPN app — not just the bot
    UI — which is what makes the reportable #CODE usable in practice.
    """
    parts = urlsplit(uri)
    return urlunsplit(parts._replace(fragment=quote(display_name, safe="")))
