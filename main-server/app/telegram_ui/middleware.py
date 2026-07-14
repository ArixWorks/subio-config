"""Presentation-layer middleware for predictable navigation state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, TelegramObject

from app.telegram_ui.context import BotServices

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class ClearNavigationStateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            services = data.get("services")
            if isinstance(services, BotServices) and event.from_user is not None:
                await services.subscriptions.ensure_user(
                    event.from_user.id,
                    event.from_user.username,
                    "fa",
                )
            if event.data != "report:start":
                state = data.get("state")
                if isinstance(state, FSMContext):
                    await state.clear()
        return await handler(event, data)
