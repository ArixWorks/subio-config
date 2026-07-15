"""AI-assisted parsing of free-text VPN outage reports.

Two independent signals are extracted from a user's report message:

1. The reported config's #CODE token (e.g. "#IT454") — resolved with a
   plain regex first, since the code is a deterministic, guaranteed-present
   token (see scoring_service.format_config_name); the AI is only asked to
   help when the user typed the digits without the leading country letters
   or without the "#".
2. The mobile operator the user is on — free text in Persian, English, or
   Finglish (e.g. "irancell", "ایرانسل", "irancel ro kar nemikone").

Both signals degrade gracefully to heuristics when the AI Gateway is
disabled, so the report flow never hard-depends on it.
"""

from __future__ import annotations

import re
from typing import Any

from app.ai.gateway import AIGateway

SYSTEM = """You analyze Iranian VPN outage reports written in Persian, English, or Finglish (Persian typed with Latin letters).
Return strict JSON only:
{"operator":"mci|irancell|rightel|shatel_mobile|aptel|samantel|other|unknown","operator_label":"free text if operator=other else null","config_code":"the #CODE token without the # if present else null","category":"blocked|slow|disconnect|other","confidence":0-1,"summary":"short Persian summary"}
Operator synonyms: همراه اول/hamrahe aval/hamrah aval/mci=mci; ایرانسل/irancell/irancel=irancell; رایتل/rightel=rightel; شاتل موبایل/shatel mobile=shatel_mobile; آپتل/aptel=aptel; سامانتل/samantel=samantel.
config_code look like IT454, DE12, TR900 — a 2-letter country prefix followed by digits, usually after a '#'."""

_CODE_RE = re.compile(r"#?\s*([A-Za-z]{2}\s?-?\s?\d{2,6})\b")

_OPERATOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "mci": ("همراه اول", "همراه‌اول", "hamrah", "hamrahe aval", "mci"),
    "irancell": ("ایرانسل", "irancell", "irancel", "mtn"),
    "rightel": ("رایتل", "rightel", "raytel"),
    "shatel_mobile": ("شاتل موبایل", "shatel mobile", "shatel"),
    "aptel": ("آپتل", "aptel"),
    "samantel": ("سامانتل", "samantel"),
}

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "blocked": ("مسدود", "فیلتر", "باز نمیشه", "block", "filter"),
    "slow": ("کند", "لاگ", "کندی", "slow", "lag"),
    "disconnect": ("قطع", "دراپ", "disconnect", "drop", "kar nemikone", "کار نمیکنه", "کار نمی‌کند"),
}


def extract_config_code(text: str) -> str | None:
    """Best-effort regex extraction of a #CODE token from free text.

    Runs before any AI call since it is the primary, cheap path and covers
    the overwhelming majority of reports where the user copy-pasted or typed
    the code shown in their VPN client / bot (e.g. "#IT454").
    """
    match = _CODE_RE.search(text)
    if not match:
        return None
    raw = match.group(1).upper().replace(" ", "").replace("-", "")
    if len(raw) < 3 or not raw[:2].isalpha() or not raw[2:].isdigit():
        return None
    return raw


def _heuristic_operator(text: str) -> str:
    lowered = text.lower()
    for code, keywords in _OPERATOR_KEYWORDS.items():
        if any(keyword in text or keyword in lowered for keyword in keywords):
            return code
    return "unknown"


def _heuristic_category(text: str) -> str:
    lowered = text.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in text or keyword in lowered for keyword in keywords):
            return category
    return "other"


async def infer_operator_report(gateway: AIGateway, text: str) -> dict[str, Any]:
    """Legacy entry point kept for the generic "report a problem" flow that
    is not tied to a specific config code (main menu -> گزارش مشکل)."""
    result = await infer_config_report(gateway, text)
    return {
        "operator": result["operator"],
        "category": result["category"],
        "confidence": result["confidence"],
        "summary": result["summary"],
    }


async def infer_config_report(gateway: AIGateway, text: str) -> dict[str, Any]:
    """Full report parse used by the public-sub "گزارش مشکل" flow: resolves
    both the reported config's code and the reporter's operator from a
    single free-text message, in whichever language/script the user chose.
    """
    default: dict[str, Any] = {
        "operator": _heuristic_operator(text),
        "operator_label": None,
        "config_code": extract_config_code(text),
        "category": _heuristic_category(text),
        "confidence": 0.35,
        "summary": text[:120],
    }
    if not text.strip():
        return default
    if not gateway.enabled:
        return default

    result = await gateway.chat_json(
        system=SYSTEM,
        user=text[:1500],
        tier="luna",
        max_tokens=180,
        default=default,
    )
    if not isinstance(result, dict):
        return default

    operator = str(result.get("operator") or default["operator"]).lower()
    valid_operators = {"mci", "irancell", "rightel", "shatel_mobile", "aptel", "samantel", "other", "unknown"}
    if operator not in valid_operators:
        operator = default["operator"]

    category = str(result.get("category") or default["category"]).lower()
    if category not in {"blocked", "slow", "disconnect", "other"}:
        category = default["category"]

    config_code = result.get("config_code") or default["config_code"]
    if config_code:
        config_code = str(config_code).upper().replace(" ", "").replace("#", "")
        if not (len(config_code) >= 3 and config_code[:2].isalpha() and config_code[2:].isdigit()):
            config_code = default["config_code"]

    return {
        "operator": operator,
        "operator_label": result.get("operator_label") or None,
        "config_code": config_code,
        "category": category,
        "confidence": float(result.get("confidence") or 0.5),
        "summary": str(result.get("summary") or text[:120]),
    }
