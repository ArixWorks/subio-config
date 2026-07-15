"""Unit tests for deterministic config naming and URI remark rewriting."""

from __future__ import annotations

from app.services.scoring_service import (
    TOP_SCORE_STAR_THRESHOLD,
    format_config_name,
    with_display_name,
)


def test_format_config_name_basic() -> None:
    name = format_config_name(config_code="454", country_code="it", score=60.0)
    assert name == "🇮🇹 @Config_SubBOT #IT454"


def test_format_config_name_adds_star_above_threshold() -> None:
    name = format_config_name(config_code="454", country_code="IT", score=TOP_SCORE_STAR_THRESHOLD)
    assert name == "🇮🇹⭐ @Config_SubBOT #IT454"


def test_format_config_name_no_star_below_threshold() -> None:
    name = format_config_name(config_code="454", country_code="IT", score=TOP_SCORE_STAR_THRESHOLD - 1)
    assert "⭐" not in name


def test_format_config_name_unknown_country_falls_back_to_globe() -> None:
    name = format_config_name(config_code="900", country_code="", score=50.0)
    assert name.startswith("🌍")
    assert "#XX900" in name


def test_with_display_name_overwrites_existing_fragment() -> None:
    uri = "ss://YWVzOnBhc3M@1.2.3.4:443#old-name"
    result = with_display_name(uri, "🇮🇹 @Config_SubBOT #IT454")

    assert result.startswith("ss://YWVzOnBhc3M@1.2.3.4:443#")
    assert "old-name" not in result
    # Fragment must be percent-encoded so it survives being embedded back
    # into a URI (clients decode it before rendering).
    assert "%40Config_SubBOT" in result


def test_with_display_name_adds_fragment_when_absent() -> None:
    uri = "vless://uuid@1.2.3.4:443?security=tls"
    result = with_display_name(uri, "🇩🇪 @Config_SubBOT #DE12")

    assert "#" in result
    assert result.split("#", 1)[0] == uri
