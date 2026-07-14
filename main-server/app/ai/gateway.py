"""Vercel AI Gateway client (OpenAI-compatible)."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, Literal

from openai import AsyncOpenAI

from app.config import Settings, get_settings

logger = logging.getLogger("subio.ai.gateway")

ModelTier = Literal["sol", "luna"]

_JSON_RE = re.compile(r"\{[\s\S]*\}|\[[\s\S]*\]")


class AIGateway:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: AsyncOpenAI | None = None
        if settings.ai_enabled and settings.vercel_ai_gateway_api_key:
            self._client = AsyncOpenAI(
                api_key=settings.vercel_ai_gateway_api_key,
                base_url=settings.ai_gateway_base_url.rstrip("/"),
                timeout=45.0,
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def model(self, tier: ModelTier = "luna") -> str:
        if tier == "sol":
            return self._settings.ai_model_sol
        return self._settings.ai_model_luna

    async def chat(
        self,
        *,
        system: str,
        user: str,
        tier: ModelTier = "luna",
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> str:
        if not self._client:
            return ""
        try:
            response = await self._client.chat.completions.create(
                model=self.model(tier),
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return (response.choices[0].message.content or "").strip()
        except Exception:
            logger.exception("ai_chat_failed", extra={"tier": tier})
            return ""

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        tier: ModelTier = "luna",
        temperature: float = 0.1,
        max_tokens: int = 800,
        default: Any = None,
    ) -> Any:
        text = await self.chat(
            system=system + "\nRespond with valid JSON only. No markdown.",
            user=user,
            tier=tier,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not text:
            return default
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = _JSON_RE.search(text)
            if not match:
                return default
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return default


@lru_cache
def get_gateway() -> AIGateway:
    return AIGateway(get_settings())
