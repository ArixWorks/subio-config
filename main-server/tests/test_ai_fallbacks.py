"""Lightweight tests for AI gateway fallbacks without network."""

from app.ai.classify import classify_message
from app.ai.gateway import AIGateway
from app.ai.security_filter import security_filter
from app.config import Settings


class _DummySettings:
    ai_enabled = False
    vercel_ai_gateway_api_key = None
    ai_gateway_base_url = "https://ai-gateway.vercel.sh/v1"
    ai_model_sol = "openai/gpt-5.6-sol"
    ai_model_luna = "openai/gpt-5.6-luna"


async def test_classify_heuristic() -> None:
    gw = AIGateway.__new__(AIGateway)
    gw._settings = _DummySettings()  # type: ignore[attr-defined]
    gw._client = None
    out = await classify_message(gw, text="here is vless://abc@host:443?security=tls")
    assert out["relevant"] is True


async def test_security_blocks_localhost() -> None:
    gw = AIGateway.__new__(AIGateway)
    gw._settings = _DummySettings()  # type: ignore[attr-defined]
    gw._client = None
    out = await security_filter(gw, "vless://id@127.0.0.1:443?security=tls")
    assert out["safe"] is False
