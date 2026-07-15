"""Presentation-layer middleware for predictable navigation state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, TelegramObject

from app.telegram_ui.context import BotServices

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

# Callbacks that lead into a multi-step FSM flow (the next update from this
# user is expected to be a free-text message, e.g. the report detail or a
# custom operator name) must not have their state wiped by this middleware —
# the state is set by the handler itself right after this runs.
_STATE_PRESERVING_PREFIXES = ("report:start", "operator:select:other")


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
            callback_data = event.data or ""
            if not any(callback_data.startswith(prefix) for prefix in _STATE_PRESERVING_PREFIXES):
                state = data.get("state")
                if isinstance(state, FSMContext):
                    await state.clear()
        return await handler(event, data)
