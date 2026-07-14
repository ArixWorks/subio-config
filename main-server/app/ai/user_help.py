"""AI-8: Smart user-facing error help."""

from __future__ import annotations

from app.ai.gateway import AIGateway

SYSTEM = """You write short Persian help for SubIO Telegram bot users when an error happens.
Return JSON: {"message":"..."}
Max 280 chars. Empathetic, concrete next step (retry / wait / check channels). No jargon."""


async def smart_error_help(
    gateway: AIGateway,
    *,
    error_code: str,
    language: str = "fa",
    context: str = "",
) -> str:
    defaults = {
        "tester_timeout": "اتصال به تستر موقتاً قطع است. لطفاً ۳۰ ثانیه دیگر دوباره تلاش کنید.",
        "no_configs": "فعلاً کانفیگ عمومی فعالی نیست. کمی بعد دوباره سر بزنید.",
        "forced_channels": "برای ادامه باید عضو کانال‌های اجباری باشید.",
        "generic": "خطای موقت رخ داد. لطفاً دوباره تلاش کنید.",
    }
    fallback = defaults.get(error_code, defaults["generic"])
    if language.startswith("en"):
        fallback = {
            "tester_timeout": "Tester is temporarily unreachable. Please retry in 30 seconds.",
            "no_configs": "No public configs available right now. Try again shortly.",
            "forced_channels": "Please join the required channels to continue.",
            "generic": "A temporary error occurred. Please try again.",
        }.get(error_code, "A temporary error occurred. Please try again.")
    if not gateway.enabled:
        return fallback
    result = await gateway.chat_json(
        system=SYSTEM,
        user=f"error_code={error_code}\nlanguage={language}\ncontext={context}",
        tier="luna",
        max_tokens=200,
        default={"message": fallback},
    )
    if not isinstance(result, dict):
        return fallback
    msg = str(result.get("message") or "").strip()
    return msg if msg else fallback
