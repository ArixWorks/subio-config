"""Inline keyboards for the Persian bot UI."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.emoji_service import EmojiService


async def main_menu(emoji: EmojiService) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                await emoji.button(
                    "private",
                    "دریافت اشتراک اختصاصی",
                    "private:locations",
                    color_key="btn_color_primary",
                )
            ],
            [
                await emoji.button("public", "ساب عمومی", "public:home"),
                await emoji.button("account", "اشتراک من", "account:home"),
            ],
            [
                await emoji.button(
                    "referral", "دعوت دوستان", "referral:home", color_key="btn_color_success"
                ),
                await emoji.button("help", "راهنما", "help:home"),
            ],
            [
                await emoji.button(
                    "report",
                    "گزارش مشکل",
                    "report:start",
                    color_key="btn_color_danger",
                )
            ],
        ]
    )


async def back_menu(emoji: EmojiService) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [await emoji.button("back", "بازگشت به منوی اصلی", "menu:home")]
        ]
    )


async def public_actions(emoji: EmojiService, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="باز کردن لینک ساب",
                    url=url,
                    style="primary",  # type: ignore[arg-type]
                )
            ],
            [
                await emoji.button("refresh", "به‌روزرسانی وضعیت", "public:home"),
                await emoji.button("back", "منوی اصلی", "menu:home"),
            ],
        ]
    )


async def public_retry(emoji: EmojiService) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                await emoji.button(
                    "refresh",
                    "تلاش دوباره",
                    "public:home",
                    color_key="btn_color_primary",
                )
            ],
            [await emoji.button("back", "منوی اصلی", "menu:home")],
        ]
    )


async def locations(
    emoji: EmojiService, items: list[dict[str, str]]
) -> InlineKeyboardMarkup:
    buttons = [
        await emoji.button(
            item["flag_emoji_key"],
            item["name_fa"],
            f"private:select:{item['code']}",
            color_key="btn_color_primary",
            fallback=item.get("fallback_flag", "🌍"),
        )
        for item in items
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append([await emoji.button("back", "بازگشت", "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def confirm_location(
    emoji: EmojiService, location_code: str
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                await emoji.button(
                    "success",
                    "ساخت اشتراک این کشور",
                    f"private:create:{location_code}",
                    color_key="btn_color_success",
                )
            ],
            [
                await emoji.button("back", "تغییر کشور", "private:locations"),
                await emoji.button("home", "منوی اصلی", "menu:home"),
            ],
        ]
    )


async def private_result(emoji: EmojiService, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="باز کردن لینک اختصاصی",
                    url=url,
                    style="success",  # type: ignore[arg-type]
                )
            ],
            [
                await emoji.button("refresh", "وضعیت اشتراک", "account:home"),
                await emoji.button("home", "منوی اصلی", "menu:home"),
            ],
        ]
    )
