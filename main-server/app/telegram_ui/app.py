"""Telegram application bootstrap."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand
from redis.asyncio import Redis

from app.config import get_settings
from app.db import Database
from app.logging import configure_logging
from app.middleware.forced_channels import ForcedChannelMiddleware
from app.s3_transport import S3FallbackStore
from app.security import PayloadCipher
from app.services.distribution_service import DistributionService
from app.services.emoji_service import EmojiService
from app.services.forced_channel_service import ForcedChannelService
from app.services.message_service import MessageService
from app.services.panel_service import PanelService
from app.services.subscription_service import SubscriptionService
from app.services.subscription_sync import SubscriptionSyncService
from app.telegram_ui.context import BotServices
from app.telegram_ui.handlers import router
from app.telegram_ui.middleware import ClearNavigationStateMiddleware


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    db = Database(settings.database_url)
    cache = Redis.from_url(settings.redis_url, decode_responses=True)
    cipher = PayloadCipher(settings.payload_encryption_key)
    panels = PanelService(db, cipher)
    subscriptions = SubscriptionService(db, panels)
    channels = ForcedChannelService(db, cache, panels)
    fallback = None
    if settings.s3_enabled:
        fallback = S3FallbackStore(
            endpoint=settings.arvan_s3_endpoint or "",
            region=settings.arvan_s3_region,
            bucket=settings.arvan_s3_bucket or "",
            access_key=settings.arvan_s3_access_key or "",
            secret_key=settings.arvan_s3_secret_key or "",
        )
    services = BotServices(
        db=db,
        redis=cache,
        settings=settings,
        emoji=EmojiService(db, cache),
        messages=MessageService(db),
        panels=panels,
        subscriptions=subscriptions,
        subscription_sync=SubscriptionSyncService(
            db,
            cipher,
            tester_base_url=str(settings.tester_base_url),
            hmac_key=settings.internal_hmac_key,
            s3=fallback,
        ),
        distribution=DistributionService(db),
        channels=channels,
    )
    bot = Bot(settings.bot_token)
    dispatcher = Dispatcher(storage=RedisStorage(cache))
    dispatcher.message.middleware(ForcedChannelMiddleware(channels, settings))
    dispatcher.callback_query.middleware(ForcedChannelMiddleware(channels, settings))
    dispatcher.callback_query.middleware(ClearNavigationStateMiddleware())
    dispatcher.include_router(router)
    dispatcher.workflow_data.update({"services": services})
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="شروع بات"),
                BotCommand(command="menu", description="نمایش منوی اصلی"),
                BotCommand(command="help", description="راهنمای سرویس‌ها"),
                BotCommand(command="cancel", description="لغو عملیات جاری"),
            ]
        )
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        with suppress(Exception):
            await dispatcher.storage.close()
        with suppress(Exception):
            await bot.session.close()
        with suppress(Exception):
            await cache.aclose()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
