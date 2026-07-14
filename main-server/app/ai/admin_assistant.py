"""AI-5: Admin assistant with operational context."""

from __future__ import annotations

from typing import Any

from app.ai.gateway import AIGateway

SYSTEM = """You are SubIO VPN operations assistant for admins.
Answer in Persian unless asked otherwise. Be concise and actionable.
Use only the provided system snapshot — do not invent metrics."""


async def admin_assist(
    gateway: AIGateway,
    *,
    question: str,
    snapshot: dict[str, Any],
) -> str:
    if not gateway.enabled:
        return (
            "AI خاموش است. وضعیت خام:\n"
            + "\n".join(f"• {k}: {v}" for k, v in list(snapshot.items())[:20])
        )
    answer = await gateway.chat(
        system=SYSTEM,
        user=f"snapshot:\n{snapshot}\n\nquestion:\n{question}",
        tier="sol",
        temperature=0.3,
        max_tokens=1000,
    )
    return answer or "پاسخی از مدل دریافت نشد."
