"""Aiogram bot handlers."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, ErrorEvent, InlineKeyboardMarkup, Message
from redis.asyncio import Redis

from app.ai.admin_assistant import admin_assist
from app.ai.gateway import get_gateway
from app.ai.i18n import translate_ui
from app.ai.reports import infer_operator_report
from app.ai.user_help import smart_error_help
from app.communication import CommunicationUnavailable
from app.config import get_settings
from app.db import Database
from app.logging import configure_logging
from app.middleware.forced_channels import ForcedChannelMiddleware
from app.security import PayloadCipher
from app.services.distribution_service import DistributionService
from app.services.emoji_service import EmojiService
from app.services.forced_channel_service import VERIFY_CALLBACK, ForcedChannelService
from app.services.message_service import MessageService
from app.services.panel_service import PanelService
from app.services.subscription_service import SubscriptionService

router = Router()
logger = logging.getLogger("subio.bot")


class BotContext:
    db: Database
    cache: Redis
    emoji: EmojiService
    messages: MessageService
    subscriptions: SubscriptionService
    distribution: DistributionService
    channels: ForcedChannelService
    settings: object

    @classmethod
    def bind(
        cls,
        db: Database,
        cache: Redis,
        subscriptions: SubscriptionService,
        distribution: DistributionService,
        channels: ForcedChannelService,
    ) -> None:
        cls.db = db
        cls.cache = cache
        cls.emoji = EmojiService(db, cache)
        cls.messages = MessageService(db)
        cls.subscriptions = subscriptions
        cls.distribution = distribution
        cls.channels = channels
        cls.settings = get_settings()


async def _t(text: str, lang: str) -> str:
    return await translate_ui(get_gateway(), text=text, target_lang=lang, redis=BotContext.cache)


async def _main_menu(message: Message, lang: str) -> None:
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                await BotContext.emoji.button(
                    "public",
                    await _t("کانفیگ‌های عمومی", lang),
                    "configs:public",
                    language=lang,
                    color_key="btn_color_primary",
                )
            ],
            [
                await BotContext.emoji.button(
                    "private",
                    await _t("اشتراک من", lang),
                    "subscription:mine",
                    language=lang,
                    color_key="btn_color_success",
                )
            ],
            [
                await BotContext.emoji.button(
                    "referral",
                    await _t("دعوت دوستان", lang),
                    "referral:mine",
                    language=lang,
                    color_key="btn_color_success",
                )
            ],
            [
                await BotContext.emoji.button(
                    "report",
                    await _t("گزارش خرابی", lang),
                    "report:start",
                    language=lang,
                    color_key="btn_color_danger",
                )
            ],
        ]
    )
    welcome = await BotContext.emoji.text("success", lang, "👋")
    body = await _t("به SubIO خوش آمدید. یک گزینه را انتخاب کنید:", lang)
    await message.answer(f"{welcome} {body}", reply_markup=keyboard)


@router.message(CommandStart())
async def start(message: Message) -> None:
    if message.from_user is None:
        return
    args = (message.text or "").split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer = int(args[1].removeprefix("ref_"))
            await BotContext.subscriptions.register_referral(message.from_user.id, referrer)
        except ValueError:
            pass
    await BotContext.subscriptions.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.language_code or "fa",
    )
    lang = message.from_user.language_code or "fa"
    ok, missing = await BotContext.channels.check_membership(message.bot, message.from_user.id)
    if not ok:
        keyboard = await BotContext.channels.join_keyboard(missing)
        help_text = await smart_error_help(
            get_gateway(), error_code="forced_channels", language=lang
        )
        await message.answer(help_text, reply_markup=keyboard)
        return
    await _main_menu(message, lang)


@router.callback_query(F.data == VERIFY_CALLBACK)
async def verify_channels(query: CallbackQuery) -> None:
    if query.from_user is None or query.message is None:
        return
    ok, missing = await BotContext.channels.check_membership(
        query.bot, query.from_user.id, use_cache=False
    )
    lang = query.from_user.language_code or "fa"
    if not ok:
        await query.answer(await _t("هنوز در همه کانال‌ها عضو نیستید.", lang), show_alert=True)
        keyboard = await BotContext.channels.join_keyboard(missing)
        await query.message.edit_text(
            await smart_error_help(get_gateway(), error_code="forced_channels", language=lang),
            reply_markup=keyboard,
        )
        return
    await query.answer(await _t("عضویت تأیید شد.", lang))
    await _main_menu(query.message, lang)


@router.callback_query(F.data == "configs:public")
async def public_configs(query: CallbackQuery) -> None:
    await query.answer()
    if query.from_user is None or query.message is None:
        return
    lang = query.from_user.language_code or "fa"
    user = await BotContext.db.fetch_one(
        "SELECT mobile_operator FROM users WHERE telegram_id=:id",
        {"id": query.from_user.id},
    )
    operator = str(user["mobile_operator"]) if user else "unknown"
    configs = await BotContext.distribution.top_configs(operator)
    if not configs:
        err = await BotContext.emoji.text("error", lang, "⚠️")
        help_text = await smart_error_help(get_gateway(), error_code="no_configs", language=lang)
        await query.message.answer(f"{err} {help_text}")
        return
    lines = [f"• {item['name']} ({item['score']})" for item in configs]
    ok = await BotContext.emoji.text("public", lang, "📡")
    title = await _t("بهترین کانفیگ‌های عمومی:", lang)
    await query.message.answer(f"{ok} {title}\n\n" + "\n".join(lines))


@router.callback_query(F.data == "subscription:mine")
async def my_subscription(query: CallbackQuery) -> None:
    await query.answer()
    if query.from_user is None or query.message is None:
        return
    lang = query.from_user.language_code or "fa"
    try:
        async with asyncio.timeout(10):
            sub = await BotContext.subscriptions.get_active(query.from_user.id)
            if not sub:
                sub = await BotContext.subscriptions.create_daily_subscription(query.from_user.id)
    except (TimeoutError, CommunicationUnavailable):
        text = await smart_error_help(
            get_gateway(),
            error_code="tester_timeout",
            language=lang,
            context="subscription",
        )
        retry = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    await BotContext.emoji.button(
                        "loading",
                        await _t("تلاش مجدد", lang),
                        "subscription:mine",
                        language=lang,
                        color_key="btn_color_primary",
                    )
                ]
            ]
        )
        await query.message.answer(text, reply_markup=retry)
        return
    settings = get_settings()
    url = await BotContext.subscriptions.subscription_url(sub["token"], str(settings.public_base_url))
    ok = await BotContext.emoji.text("success", lang, "✅")
    await query.message.answer(
        f"{ok} {await _t('اشتراک فعال', lang)}\n"
        f"{await _t('انقضا', lang)}: {sub['expires_at']}\n"
        f"{await _t('مصرف', lang)}: {sub['volume_used_bytes']} / {sub['volume_limit_bytes']}\n"
        f"{await _t('لینک ساب', lang)}: {url}"
    )


@router.callback_query(F.data == "referral:mine")
async def referral(query: CallbackQuery) -> None:
    await query.answer()
    if query.message is None or query.from_user is None:
        return
    bot = await query.bot.get_me()
    lang = query.from_user.language_code or "fa"
    emoji = await BotContext.emoji.text("referral", lang, "👥")
    await query.message.answer(
        f"{emoji} {await _t('لینک دعوت', lang)}:\nhttps://t.me/{bot.username}?start=ref_{query.from_user.id}"
    )


@router.callback_query(F.data == "report:start")
async def report_start(query: CallbackQuery) -> None:
    await query.answer()
    if query.message is None or query.from_user is None:
        return
    lang = query.from_user.language_code or "fa"
    await query.message.answer(
        await _t(
            "شناسه کانفیگ (UUID) را بفرستید، یا متن فارسی مشکل را توضیح دهید "
            "(مثلاً: روی ایرانسل قطع شده).",
            lang,
        )
    )


@router.message(F.text.regexp(r"^[0-9a-f-]{36}$"))
async def report_config(message: Message) -> None:
    if message.from_user is None:
        return
    config_id = (message.text or "").strip()
    row = await BotContext.db.fetch_one("SELECT id FROM vpn_configs WHERE id=:id", {"id": config_id})
    if not row:
        await message.answer(await _t("کانفیگ یافت نشد.", message.from_user.language_code or "fa"))
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                await BotContext.emoji.button("report", "مسدود", f"report:blocked:{config_id}", color_key="btn_color_danger"),
                await BotContext.emoji.button("report", "کند", f"report:slow:{config_id}", color_key="btn_color_primary"),
            ],
            [
                await BotContext.emoji.button("report", "قطع", f"report:disconnect:{config_id}", color_key="btn_color_danger"),
                await BotContext.emoji.button("report", "سایر", f"report:other:{config_id}", color_key="btn_color_primary"),
            ],
        ]
    )
    await message.answer("دسته گزارش را انتخاب کنید:", reply_markup=keyboard)


@router.message(F.text & ~F.text.startswith("/"))
async def freeform_report_or_ignore(message: Message) -> None:
    """AI-4: free-text Persian outage notes without UUID."""
    if message.from_user is None or not message.text:
        return
    text = message.text.strip()
    if len(text) < 8 or len(text) > 500:
        return
    # Avoid hijacking ordinary chatter: require outage keywords.
    keywords = ("قطع", "مسدود", "کار نمیکنه", "کار نمی‌کنه", "کند", "ایرانسل", "همراه", "رایتل", "vpn", "کانفیگ")
    if not any(k in text.lower() or k in text for k in keywords):
        return
    await BotContext.subscriptions.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.language_code or "fa",
    )
    inferred = await infer_operator_report(get_gateway(), text)
    if inferred.get("operator") and inferred["operator"] != "unknown":
        await BotContext.db.execute(
            "UPDATE users SET mobile_operator=:operator WHERE telegram_id=:id",
            {"operator": inferred["operator"], "id": message.from_user.id},
        )
    try:
        await BotContext.db.execute(
            """
            INSERT INTO user_reports(user_id, config_id, category, detail)
            VALUES (:user_id, NULL, :category, :detail)
            """,
            {
                "user_id": message.from_user.id,
                "category": inferred.get("category") or "other",
                "detail": inferred.get("summary") or text[:500],
            },
        )
    except Exception:
        logger.exception("freeform_report_insert_failed")
        await message.answer("ثبت گزارش ناموفق بود. لطفاً با UUID کانفیگ گزارش دهید.")
        return
    lang = message.from_user.language_code or "fa"
    await message.answer(
        await _t(
            f"گزارش ثبت شد. اپراتور تشخیص‌داده‌شده: {inferred.get('operator')} / "
            f"دسته: {inferred.get('category')}",
            lang,
        )
    )


@router.callback_query(F.data.startswith("report:"))
async def report_category(query: CallbackQuery) -> None:
    await query.answer()
    if query.from_user is None or query.message is None or not query.data:
        return
    parts = query.data.split(":")
    if len(parts) < 3 or parts[1] == "start":
        return
    _, category, config_id = parts[0], parts[1], parts[2]
    await BotContext.db.execute(
        """
        INSERT INTO user_reports(user_id, config_id, category)
        VALUES (:user_id, :config_id, :category)
        """,
        {"user_id": query.from_user.id, "config_id": config_id, "category": category},
    )
    await BotContext.db.execute(
        "UPDATE vpn_configs SET score=GREATEST(score - 5, 0) WHERE id=:id",
        {"id": config_id},
    )
    ok = await BotContext.emoji.text("success", query.from_user.language_code or "fa", "✅")
    await query.message.answer(f"{ok} گزارش شما ثبت شد.")


@router.message(Command("operator"))
async def set_operator(message: Message) -> None:
    if message.from_user is None:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("استفاده: /operator mci|irancell|rightel")
        return
    operator = parts[1].strip().lower()
    if operator not in {"mci", "irancell", "rightel", "unknown"}:
        await message.answer("اپراتور نامعتبر.")
        return
    await BotContext.db.execute(
        "UPDATE users SET mobile_operator=:operator WHERE telegram_id=:id",
        {"operator": operator, "id": message.from_user.id},
    )
    await message.answer(f"اپراتور شما روی {operator} تنظیم شد.")


@router.message(Command("ask"))
async def admin_ask(message: Message) -> None:
    if message.from_user is None:
        return
    settings = get_settings()
    if message.from_user.id not in settings.admin_ids:
        return
    question = (message.text or "").removeprefix("/ask").strip()
    if not question:
        await message.answer("استفاده: /ask وضعیت تستر چطوره؟")
        return
    healthy = await BotContext.db.fetch_one(
        "SELECT COUNT(*) AS c FROM vpn_configs WHERE scope='public' AND is_enabled AND score>=50"
    )
    dead = await BotContext.db.fetch_one(
        "SELECT COUNT(*) AS c FROM vpn_configs WHERE scope='public' AND (NOT is_enabled OR score<50)"
    )
    snapshot = {
        "healthy_configs": int((healthy or {}).get("c") or 0),
        "dead_configs": int((dead or {}).get("c") or 0),
        "ai_enabled": get_gateway().enabled,
        "models": {"sol": settings.ai_model_sol, "luna": settings.ai_model_luna},
    }
    answer = await admin_assist(get_gateway(), question=question, snapshot=snapshot)
    await message.answer(answer[:4000])


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    logger.exception("bot_handler_error", exc_info=event.exception)
    lang = "fa"
    if event.update.message and event.update.message.from_user:
        lang = event.update.message.from_user.language_code or "fa"
    text = await smart_error_help(get_gateway(), error_code="generic", language=lang)
    message = event.update.message
    if message:
        await message.answer(text)
    elif event.update.callback_query and event.update.callback_query.message:
        await event.update.callback_query.message.answer(text)
    return True


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    db = Database(settings.database_url)
    cache = Redis.from_url(settings.redis_url, decode_responses=True)
    cipher = PayloadCipher(settings.payload_encryption_key)
    panels = PanelService(db, cipher)
    subscriptions = SubscriptionService(db, panels)
    distribution = DistributionService(db)
    channels = ForcedChannelService(db, cache)
    BotContext.bind(db, cache, subscriptions, distribution, channels)
    bot = Bot(settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.message.middleware(ForcedChannelMiddleware(channels, settings))
    dispatcher.callback_query.middleware(ForcedChannelMiddleware(channels, settings))
    dispatcher.include_router(router)
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        with suppress(Exception):
            await bot.session.close()
        await cache.aclose()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
