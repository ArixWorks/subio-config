"""Health score computation and operator-aware adjustments."""

from __future__ import annotations

from typing import Any


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
    config_id: int,
    location: str,
    protocol: str,
    latency_ms: int | None,
    score: float,
    transport: str | None = None,
) -> str:
    flag = {"DE": "🇩🇪", "US": "🇺🇸", "TR": "🇹🇷", "NL": "🇳🇱"}.get(location.upper(), "🌍")
    latency = f"{latency_ms}ms" if latency_ms else "—"
    transport_label = transport or protocol.upper()
    stars = "★" if score >= 90 else "☆"
    return f"{flag} SubIO #{config_id} | {transport_label} | {latency} | {stars}{int(score)}"
