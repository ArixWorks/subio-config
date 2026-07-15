"""Heuristic-path tests for AI-assisted report parsing (no network)."""

from __future__ import annotations

import pytest

from app.ai.reports import extract_config_code, infer_config_report


class _DisabledGateway:
    enabled = False


@pytest.mark.asyncio
async def test_extracts_code_with_hash_and_country_prefix() -> None:
    assert extract_config_code("Config #IT454 roy irancell kar nemikone") == "IT454"


@pytest.mark.asyncio
async def test_extracts_code_without_hash() -> None:
    assert extract_config_code("کانفیگ DE12 روی همراه اول کار نمیکنه") == "DE12"


@pytest.mark.asyncio
async def test_extract_code_returns_none_when_absent() -> None:
    assert extract_config_code("اینترنت اصلا کار نمیکنه") is None


@pytest.mark.asyncio
async def test_infer_config_report_persian_heuristic() -> None:
    result = await infer_config_report(
        _DisabledGateway(), "کانفیگ #IT454 روی ایرانسل کار نمیکنه"
    )
    assert result["config_code"] == "IT454"
    assert result["operator"] == "irancell"
    assert result["category"] == "disconnect"


@pytest.mark.asyncio
async def test_infer_config_report_finglish_heuristic() -> None:
    result = await infer_config_report(
        _DisabledGateway(), "Config #DE12 roy irancell kar nemikone"
    )
    assert result["config_code"] == "DE12"
    assert result["operator"] == "irancell"


@pytest.mark.asyncio
async def test_infer_config_report_english_blocked() -> None:
    result = await infer_config_report(
        _DisabledGateway(), "config #TR900 is blocked on mci"
    )
    assert result["config_code"] == "TR900"
    assert result["operator"] == "mci"
    assert result["category"] == "blocked"


@pytest.mark.asyncio
async def test_infer_config_report_unknown_operator_defaults() -> None:
    result = await infer_config_report(_DisabledGateway(), "#IT454 slow")
    assert result["config_code"] == "IT454"
    assert result["operator"] == "unknown"
    assert result["category"] == "slow"
