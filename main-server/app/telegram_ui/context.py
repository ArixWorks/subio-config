"""Injected dependencies for Telegram handlers."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.config import Settings
from app.db import Database
from app.services.distribution_service import DistributionService
from app.services.emoji_service import EmojiService
from app.services.forced_channel_service import ForcedChannelService
from app.services.message_service import MessageService
from app.services.panel_service import PanelService
from app.services.subscription_service import SubscriptionService
from app.services.subscription_sync import SubscriptionSyncService


@dataclass(frozen=True)
class BotServices:
    db: Database
    redis: Redis
    settings: Settings
    emoji: EmojiService
    messages: MessageService
    panels: PanelService
    subscriptions: SubscriptionService
    subscription_sync: SubscriptionSyncService
    distribution: DistributionService
    channels: ForcedChannelService
