"""Block bot access until user joins all forced channels."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import Settings
from app.services.forced_channel_service import VERIFY_CALLBACK, ForcedChannelService

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class ForcedChannelMiddleware(BaseMiddleware):
    def __init__(self, service: ForcedChannelService, settings: Settings) -> None:
        self._service = service
        self._admin_ids = settings.admin_ids

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        bot: Bot | None = data.get("bot")
        if user is None or bot is None or user.id in self._admin_ids:
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.data == VERIFY_CALLBACK:
            return await handler(event, data)

        if isinstance(event, Message) and event.text:
            command = event.text.split(maxsplit=1)[0].split("@", 1)[0]
            if command in {"/start", "/cancel"}:
                return await handler(event, data)

        ok, missing = await self._service.check_membership(bot, user.id)
        if ok:
            return await handler(event, data)

        keyboard = await self._service.join_keyboard(missing)
        text = (
            "برای استفاده از ربات باید در کانال‌های زیر عضو شوید.\n"
            "پس از عضویت، «عضو شدم» را بزنید."
        )
        if isinstance(event, CallbackQuery):
            await event.answer("ابتدا در کانال‌ها عضو شوید.", show_alert=True)
            if isinstance(event.message, Message):
                try:
                    await event.message.edit_text(text, reply_markup=keyboard)
                except TelegramBadRequest:
                    await event.message.answer(text, reply_markup=keyboard)
            return None
        if isinstance(event, Message):
            await event.answer(text, reply_markup=keyboard)
            return None
        return await handler(event, data)
