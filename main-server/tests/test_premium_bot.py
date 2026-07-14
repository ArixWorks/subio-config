from datetime import UTC, datetime

import pytest

from app.formatting import format_expiry
from app.services.emoji_service import EmojiService
from app.services.panel_service import country_flag, next_tehran_midnight, tehran_service_day


class FakeDatabase:
    def __init__(self, row):
        self.row = row

    async def fetch_one(self, statement, values):
        return self.row


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)


def test_next_tehran_midnight_is_exact_utc_boundary() -> None:
    now = datetime(2026, 7, 14, 18, 0, tzinfo=UTC)
    expiry = next_tehran_midnight(now)

    assert expiry == datetime(2026, 7, 14, 20, 30, tzinfo=UTC)
    assert tehran_service_day(now).isoformat() == "2026-07-14"


def test_format_expiry_uses_tehran_time() -> None:
    value = datetime(2026, 7, 14, 20, 30, tzinfo=UTC)

    assert format_expiry(value, "fa") == "2026-07-15 00:00 به وقت تهران"


def test_country_flag_fallback() -> None:
    assert country_flag("DE") == "🇩🇪"
    assert country_flag("invalid") == "🌍"


@pytest.mark.asyncio
async def test_custom_emoji_renders_utf16_entity_and_fallback() -> None:
    service = EmojiService(
        FakeDatabase(
            {
                "value": "5368324170671202286",
                "type": "custom_emoji",
                "fallback_value": "🇩🇪",
            }
        ),
        FakeRedis(),
    )

    text, entities = await service.render("flag_de", "آلمان")

    assert text == "🇩🇪 آلمان"
    assert entities is not None
    assert entities[0].offset == 0
    assert entities[0].length == 4
    assert entities[0].custom_emoji_id == "5368324170671202286"


@pytest.mark.asyncio
async def test_emoji_cache_invalidation_normalizes_key() -> None:
    redis = FakeRedis()
    redis.values["ui:fa:emoji_public"] = "📡|emoji|"
    service = EmojiService(FakeDatabase(None), redis)

    await service.invalidate("public")

    assert "ui:fa:emoji_public" not in redis.values
