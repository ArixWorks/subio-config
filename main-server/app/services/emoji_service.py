"""Runtime UI asset resolution for emoji, custom emoji, and button colors."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, MessageEntity
from redis.asyncio import Redis

from app.db import Database


@dataclass(frozen=True)
class UIAsset:
    value: str
    type: str


class EmojiService:
    def __init__(self, db: Database, cache: Redis) -> None:
        self._db = db
        self._cache = cache

    async def get(self, key: str, language: str = "fa") -> UIAsset:
        full_key = key if key.startswith("emoji_") or key.startswith("btn_") else f"emoji_{key}"
        cache_key = f"ui:{language}:{full_key}"
        cached = await self._cache.get(cache_key)
        if cached:
            parts = str(cached).split("|", 1)
            return UIAsset(parts[0], parts[1] if len(parts) > 1 else "emoji")
        row = await self._db.fetch_one(
            "SELECT value, type FROM ui_assets WHERE key=:key AND language=:language",
            {"key": full_key, "language": language},
        )
        if not row:
            return UIAsset("", "emoji")
        asset = UIAsset(str(row["value"]), str(row["type"]))
        await self._cache.set(cache_key, f"{asset.value}|{asset.type}", ex=300)
        return asset

    async def text(self, key: str, language: str = "fa", fallback: str = "") -> str:
        asset = await self.get(key, language)
        if asset.type == "custom_emoji":
            return fallback or "•"
        return asset.value or fallback

    async def button(
        self,
        key: str,
        label: str,
        callback_data: str,
        *,
        language: str = "fa",
        color_key: str | None = None,
    ) -> InlineKeyboardButton:
        emoji = await self.text(key, language)
        text = f"{emoji} {label}".strip() if emoji else label
        asset = await self.get(key, language)
        kwargs: dict[str, object] = {"text": text, "callback_data": callback_data}
        if asset.type == "custom_emoji" and asset.value.isdigit():
            kwargs["icon_custom_emoji_id"] = asset.value
            kwargs["text"] = label
        if color_key:
            color_asset = await self.get(color_key, language)
            if color_asset.type == "color" and color_asset.value in {
                "primary",
                "success",
                "danger",
                "secondary",
            }:
                kwargs["style"] = color_asset.value
        return InlineKeyboardButton(**kwargs)  # type: ignore[arg-type]

    async def entities(self, key: str, language: str = "fa") -> list[MessageEntity]:
        asset = await self.get(key, language)
        if asset.type != "custom_emoji" or not asset.value.isdigit():
            return []
        return [
            MessageEntity(type="custom_emoji", offset=0, length=1, custom_emoji_id=asset.value)
        ]

    async def invalidate(self, key: str, language: str = "fa") -> None:
        await self._cache.delete(f"ui:{language}:{key}")
