"""Persian-first Telegram handlers."""

from __future__ import annotations

import asyncio
import logging
import re
from html import escape

from aiogram import F, Router
from aiogram.enums import MessageEntityType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardMarkup,
    Message,
)

from app.ai.admin_assistant import admin_assist
from app.ai.gateway import get_gateway
from app.ai.reports import infer_config_report, infer_operator_report
from app.communication import CommunicationUnavailable
from app.formatting import format_bytes, format_expiry, format_volume_pair
from app.services.panel_service import DAILY_BASE_BYTES, next_tehran_midnight
from app.services.forced_channel_service import VERIFY_CALLBACK
from app.services.qr_service import generate_branded_qr
from app.telegram_ui import keyboards, texts
from app.telegram_ui.context import BotServices

router = Router(name="persian-ui")
logger = logging.getLogger("subio.bot.ui")
CUSTOM_EMOJI_TAG = re.compile(
    r'<tg-emoji emoji-id="[0-9]+">([^<]+)</tg-emoji>'
)


class ReportFlow(StatesGroup):
    waiting_for_detail = State()
    waiting_for_public_detail = State()


class OperatorFlow(StatesGroup):
    waiting_for_custom_name = State()


def _telegram_fallback(
    text: str, reply_markup: InlineKeyboardMarkup | None
) -> tuple[str, InlineKeyboardMarkup | None]:
    plain_text = CUSTOM_EMOJI_TAG.sub(r"\1", text)
    if reply_markup is None:
        return plain_text, None
    rows = [
        [
            button.model_copy(
                update={"icon_custom_emoji_id": None, "style": None}
            )
            for button in row
        ]
        for row in reply_markup.inline_keyboard
    ]
    return plain_text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _edit_or_answer(
    target: object,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if not isinstance(target, Message):
        return
    try:
        await target.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest:
        fallback_text, fallback_markup = _telegram_fallback(text, reply_markup)
        await target.answer(
            fallback_text, reply_markup=fallback_markup, parse_mode="HTML"
        )


async def _ensure_user(message: Message, services: BotServices) -> None:
    if message.from_user:
        await services.subscriptions.ensure_user(
            message.from_user.id, message.from_user.username, "fa"
        )


async def _callback_allowed(
    query: CallbackQuery, services: BotServices, action: str
) -> bool:
    if query.from_user is None:
        return False
    key = f"bot:callback:{action}:{query.from_user.id}"
    return bool(await services.redis.set(key, "1", ex=1, nx=True))


async def show_main(message: Message, services: BotServices, *, edit: bool = False) -> None:
    body = await services.emoji.render_html("home", texts.WELCOME, fallback="✨")
    markup = await keyboards.main_menu(services.emoji)
    if edit:
        await _edit_or_answer(message, body, reply_markup=markup)
    else:
        try:
            await message.answer(body, reply_markup=markup, parse_mode="HTML")
        except TelegramBadRequest:
            fallback_text, fallback_markup = _telegram_fallback(body, markup)
            await message.answer(
                fallback_text, reply_markup=fallback_markup, parse_mode="HTML"
            )


@router.message(CommandStart())
async def start(message: Message, services: BotServices, state: FSMContext) -> None:
    if message.from_user is None:
        return
    await state.clear()
    await _ensure_user(message, services)
    args = (message.text or "").split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            await services.subscriptions.register_referral(
                message.from_user.id, int(args[1].removeprefix("ref_"))
            )
        except ValueError:
            pass
    ok, missing = await services.channels.check_membership(
        message.bot, message.from_user.id
    )
    if not ok:
        await message.answer(
            "برای استفاده از خدمات SubIO ابتدا عضو کانال‌های زیر شوید و سپس تأیید را بزنید.",
            reply_markup=await services.channels.join_keyboard(missing),
        )
        return
    await show_main(message, services)


@router.message(Command("menu"))
async def menu_command(message: Message, services: BotServices, state: FSMContext) -> None:
    await state.clear()
    await _ensure_user(message, services)
    await show_main(message, services)


@router.message(Command("help"))
async def help_command(
    message: Message, services: BotServices, state: FSMContext
) -> None:
    await state.clear()
    await _ensure_user(message, services)
    body = await services.emoji.render_html("help", texts.HELP, fallback="🧭")
    await message.answer(
        body, reply_markup=await keyboards.back_menu(services.emoji), parse_mode="HTML"
    )


@router.message(Command("cancel"))
async def cancel_command(message: Message, services: BotServices, state: FSMContext) -> None:
    await state.clear()
    await _ensure_user(message, services)
    await show_main(message, services)


@router.callback_query(F.data == "menu:home")
async def menu_callback(
    query: CallbackQuery, services: BotServices, state: FSMContext
) -> None:
    await query.answer()
    await state.clear()
    if query.message:
        await show_main(query.message, services, edit=True)


@router.callback_query(F.data == VERIFY_CALLBACK)
async def verify_channels(query: CallbackQuery, services: BotServices) -> None:
    if query.from_user is None or query.message is None:
        return
    ok, missing = await services.channels.check_membership(
        query.bot, query.from_user.id, use_cache=False
    )
    if not ok:
        await query.answer("هنوز عضویت همه کانال‌ها تأیید نشده است.", show_alert=True)
        if isinstance(query.message, Message):
            await query.message.edit_text(
                "ابتدا عضو همه کانال‌های زیر شوید و دوباره بررسی کنید.",
                reply_markup=await services.channels.join_keyboard(missing),
            )
        return
    await services.subscriptions.ensure_user(
        query.from_user.id,
        query.from_user.username,
        "fa",
    )
    await query.answer("عضویت شما تأیید شد.")
    await show_main(query.message, services, edit=True)


@router.callback_query(F.data == "help:home")
async def help_callback(query: CallbackQuery, services: BotServices) -> None:
    await query.answer()
    if query.message:
        body = await services.emoji.render_html("help", texts.HELP, fallback="🧭")
        await _edit_or_answer(
            query.message,
            body,
            reply_markup=await keyboards.back_menu(services.emoji),
        )


@router.callback_query(F.data == "public:home")
async def public_home(query: CallbackQuery, services: BotServices) -> None:
    if not await _callback_allowed(query, services, "public"):
        await query.answer("کمی آهسته‌تر تلاش کنید.", show_alert=False)
        return
    await query.answer("در حال آماده‌سازی ساب عمومی...")
    if query.from_user is None or query.message is None:
        return
    await services.subscriptions.ensure_user(
        query.from_user.id, query.from_user.username, "fa"
    )
    feed = await services.subscriptions.get_or_create_public_feed(query.from_user.id)
    if not feed.get("operator_code"):
        # First-ever entry into the public-sub section: the operator must be
        # known before a feed is ever synced, so operator-aware filtering
        # (config_operator_exclusions) applies from the very first link the
        # user receives instead of only from their second visit onward.
        rendered = await services.emoji.render_html(
            "operator", texts.OPERATOR_PROMPT, fallback="📶"
        )
        await _edit_or_answer(
            query.message,
            rendered,
            reply_markup=await keyboards.operator_choices(services.emoji),
        )
        return
    token = str(feed["token"])
    count_row = await services.db.fetch_one(
        """
        SELECT COUNT(*) AS count FROM vpn_configs
        WHERE scope='public' AND is_enabled AND score>=50
          AND (expires_at IS NULL OR expires_at>now())
        """
    )
    count = int((count_row or {}).get("count") or 0)
    if count == 0:
        try:
            async with asyncio.timeout(10):
                await services.subscription_sync.sync_token(token)
        except Exception:
            logger.exception(
                "empty_public_feed_invalidation_failed",
                extra={"user_id": query.from_user.id},
            )
        body = (
            "<b>مخزن عمومی در حال آماده‌سازی است.</b>\n\n"
            "در حال حاضر کانفیگ سالمی برای تحویل وجود ندارد. "
            "کمی بعد دوباره تلاش کنید."
        )
        rendered = await services.emoji.render_html("error", body, fallback="⚠️")
        await _edit_or_answer(
            query.message,
            rendered,
            reply_markup=await keyboards.public_retry(services.emoji),
        )
        return
    try:
        async with asyncio.timeout(10):
            await services.subscription_sync.sync_token(token)
    except Exception:
        logger.exception(
            "public_feed_immediate_sync_failed",
            extra={"user_id": query.from_user.id},
        )
        body = (
            "<b>ارتباط با سرور تحویل ساب موقتاً برقرار نشد.</b>\n\n"
            "لینک ناقص نمایش داده نمی‌شود؛ چند لحظه دیگر دوباره تلاش کنید."
        )
        rendered = await services.emoji.render_html("error", body, fallback="⚠️")
        await _edit_or_answer(
            query.message,
            rendered,
            reply_markup=await keyboards.public_retry(services.emoji),
        )
        return
    url = await services.subscriptions.subscription_url(
        feed["token"], str(services.settings.public_base_url)
    )
    body = (
        f"{texts.PUBLIC_INFO}\n\n"
        f"<b>وضعیت مخزن:</b> {count} کانفیگ سالم\n"
        "<b>محتوای ساب:</b> ۱۰ کانفیگ برتر و سریع‌تر\n"
        f"<b>لینک اختصاصی شما:</b>\n<code>{escape(url)}</code>"
    )
    rendered = await services.emoji.render_html("public", body, fallback="📡")
    await _edit_or_answer(
        query.message,
        rendered,
        reply_markup=await keyboards.public_actions(services.emoji, url),
    )


@router.callback_query(F.data == "public:operator")
async def public_operator_prompt(query: CallbackQuery, services: BotServices) -> None:
    await query.answer()
    if query.message is None:
        return
    rendered = await services.emoji.render_html(
        "operator", texts.OPERATOR_PROMPT, fallback="📶"
    )
    await _edit_or_answer(
        query.message,
        rendered,
        reply_markup=await keyboards.operator_choices(services.emoji, back_target="public:home"),
    )


@router.callback_query(F.data.startswith("operator:select:"))
async def operator_select(
    query: CallbackQuery, services: BotServices, state: FSMContext
) -> None:
    if query.from_user is None or query.message is None or not query.data:
        await query.answer()
        return
    code = query.data.rsplit(":", 1)[-1]
    if code == texts.OPERATOR_OTHER_CODE:
        await query.answer()
        await state.set_state(OperatorFlow.waiting_for_custom_name)
        rendered = await services.emoji.render_html(
            "operator", texts.OPERATOR_OTHER_PROMPT, fallback="📶"
        )
        await _edit_or_answer(
            query.message,
            rendered,
            reply_markup=await keyboards.back_menu(services.emoji),
        )
        return
    label = texts.OPERATOR_LABELS.get(code, code)
    await services.subscriptions.get_or_create_public_feed(query.from_user.id)
    await services.subscriptions.set_public_feed_operator(query.from_user.id, code, label)
    await query.answer(texts.OPERATOR_SAVED.format(label=label))
    await public_home(query, services)


@router.message(OperatorFlow.waiting_for_custom_name, F.text)
async def operator_custom_name(
    message: Message, services: BotServices, state: FSMContext
) -> None:
    if message.from_user is None or not message.text:
        return
    label = message.text.strip()
    if not 2 <= len(label) <= 64:
        await message.answer("نام اپراتور باید بین ۲ تا ۶۴ نویسه باشد.")
        return
    code = f"{texts.OPERATOR_OTHER_CODE}:{label}"[:64]
    await services.subscriptions.get_or_create_public_feed(message.from_user.id)
    await services.subscriptions.set_public_feed_operator(message.from_user.id, code, label)
    await state.clear()
    await message.answer(
        texts.OPERATOR_SAVED.format(label=escape(label)),
        parse_mode="HTML",
        reply_markup=await keyboards.back_menu(services.emoji),
    )


@router.message(OperatorFlow.waiting_for_custom_name)
async def operator_custom_name_non_text(message: Message) -> None:
    await message.answer("لطفاً نام اپراتور را فقط به‌صورت پیام متنی ارسال کنید.")


@router.callback_query(F.data == "public:change")
async def public_change_link_confirm(query: CallbackQuery, services: BotServices) -> None:
    await query.answer()
    if query.message is None:
        return
    rendered = await services.emoji.render_html(
        "change_link", texts.CHANGE_LINK_CONFIRM, fallback="♻️"
    )
    await _edit_or_answer(
        query.message,
        rendered,
        reply_markup=await keyboards.change_link_confirm(services.emoji),
    )


@router.callback_query(F.data == "public:change:confirm")
async def public_change_link_do(query: CallbackQuery, services: BotServices) -> None:
    if query.from_user is None or query.message is None:
        await query.answer()
        return
    lock_key = f"bot:public-change:{query.from_user.id}"
    if not await services.redis.set(lock_key, "1", ex=15, nx=True):
        await query.answer("درخواست قبلی هنوز در حال انجام است.", show_alert=True)
        return
    await query.answer("در حال ساخت لینک جدید...")
    try:
        await services.subscriptions.rotate_public_feed_token(query.from_user.id)
    finally:
        await services.redis.delete(lock_key)
    await query.answer(texts.CHANGE_LINK_DONE)
    await public_home(query, services)


@router.callback_query(F.data == "public:qr")
async def public_qr(query: CallbackQuery, services: BotServices) -> None:
    if query.from_user is None or query.message is None:
        await query.answer()
        return
    await query.answer("در حال ساخت QR کد...")
    feed = await services.subscriptions.get_or_create_public_feed(query.from_user.id)
    url = await services.subscriptions.subscription_url(
        feed["token"], str(services.settings.public_base_url)
    )
    try:
        qr = generate_branded_qr(url)
    except Exception:
        logger.exception("qr_generation_failed", extra={"user_id": query.from_user.id})
        await query.answer("ساخت QR کد با خطا مواجه شد.", show_alert=True)
        return
    photo = BufferedInputFile(qr.png_bytes, filename="subio-qr.png")
    await query.message.answer_photo(
        photo,
        caption=texts.QR_CAPTION,
        reply_markup=await keyboards.qr_result(services.emoji),
    )


@router.callback_query(F.data == "report:start:public")
async def public_report_start(
    query: CallbackQuery, services: BotServices, state: FSMContext
) -> None:
    if not isinstance(query.message, Message):
        await query.answer(
            "این پیام دیگر قابل استفاده نیست؛ /menu را ارسال کنید.",
            show_alert=True,
        )
        return
    await query.answer()
    await state.set_state(ReportFlow.waiting_for_public_detail)
    body = (
        "<b>گزارش مشکل کانفیگ ساب عمومی</b>\n\n"
        "کد کانفیگ (مثلاً <code>#IT454</code>) و توضیح مشکل را در یک پیام بنویسید؛ "
        "فارسی، انگلیسی یا فینگلیش، هر کدام مشکلی ندارد. برای نمونه:\n"
        "<blockquote>Config #IT454 roy irancell kar nemikone</blockquote>\n\n"
        "برای لغو، دکمه بازگشت یا دستور /cancel را بزنید."
    )
    rendered = await services.emoji.render_html("report", body, fallback="🚨")
    await _edit_or_answer(
        query.message,
        rendered,
        reply_markup=await keyboards.back_menu(services.emoji),
    )


@router.message(ReportFlow.waiting_for_public_detail, F.text)
async def public_report_detail(
    message: Message, services: BotServices, state: FSMContext
) -> None:
    if message.from_user is None or not message.text:
        return
    detail = message.text.strip()
    if not 4 <= len(detail) <= 500:
        await message.answer("توضیح مشکل باید بین ۴ تا ۵۰۰ نویسه باشد.")
        return
    inferred = await infer_config_report(get_gateway(), detail)
    config_code = inferred.get("config_code")
    if not config_code:
        not_found = await services.messages.get(
            "config_report_not_found",
            texts.OPERATOR_PROMPT,
        )
        await message.answer(not_found)
        return
    config = await services.reports.find_config_by_code(str(config_code))
    if config is None:
        not_found = await services.messages.get(
            "config_report_not_found",
            "کد کانفیگ پیدا نشد. کد داخل نام کانفیگ، بعد از # نوشته شده است؛ مثلاً #IT454.",
        )
        await message.answer(not_found)
        return

    feed_row = await services.db.fetch_one(
        "SELECT operator_code FROM public_feeds WHERE user_id=:user_id",
        {"user_id": message.from_user.id},
    )
    operator_code = str((feed_row or {}).get("operator_code") or inferred.get("operator") or "unknown")

    await state.clear()
    outcome = await services.reports.submit_report(
        config_id=str(config["id"]),
        reporter_user_id=message.from_user.id,
        operator_code=operator_code,
        detail=str(inferred.get("summary") or detail)[:500],
    )
    # Force-replace this specific user's copy of the reported config
    # immediately, regardless of whether the operator/global threshold was
    # crossed yet — satisfies "replace it for that specific user" even on a
    # single first-time report.
    await services.reports.mark_user_feed_replacement(
        user_id=message.from_user.id, config_id=str(config["id"])
    )
    token_row = await services.db.fetch_one(
        "SELECT token::text AS token FROM public_feeds WHERE user_id=:user_id",
        {"user_id": message.from_user.id},
    )
    reply_markup = await keyboards.back_menu(services.emoji)
    if token_row:
        try:
            async with asyncio.timeout(10):
                await services.subscription_sync.sync_token(str(token_row["token"]))
        except Exception:
            logger.exception(
                "public_report_resync_failed", extra={"user_id": message.from_user.id}
            )
        url = await services.subscriptions.subscription_url(
            token_row["token"], str(services.settings.public_base_url)
        )
        reply_markup = await keyboards.public_actions(services.emoji, url)
    await message.answer(
        texts.REPORT_SUBMITTED_WITH_CODE.format(code=escape(str(config_code))),
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


@router.message(ReportFlow.waiting_for_public_detail)
async def public_report_non_text(message: Message) -> None:
    await message.answer("لطفاً مشکل را فقط به‌صورت پیام متنی ارسال کنید.")


@router.callback_query(F.data == "private:locations")
async def private_locations(query: CallbackQuery, services: BotServices) -> None:
    await query.answer()
    if query.message is None:
        return
    items = await services.panels.list_locations()
    if not items:
        await _edit_or_answer(
            query.message,
            texts.NO_LOCATIONS,
            reply_markup=await keyboards.back_menu(services.emoji),
        )
        return
    rendered = await services.emoji.render_html(
        "private", texts.PRIVATE_INFO, fallback="🔐"
    )
    await _edit_or_answer(
        query.message,
        rendered,
        reply_markup=await keyboards.locations(services.emoji, items),
    )


@router.callback_query(F.data.startswith("private:select:"))
async def private_select(query: CallbackQuery, services: BotServices) -> None:
    if query.from_user is None or query.message is None or not query.data:
        await query.answer()
        return
    code = query.data.rsplit(":", 1)[-1].upper()
    item = next(
        (
            location
            for location in await services.panels.list_locations()
            if location["code"] == code
        ),
        None,
    )
    if item is None:
        await query.answer("این لوکیشن دیگر فعال نیست.", show_alert=True)
        return
    await query.answer()
    credit_row = await services.db.fetch_one(
        "SELECT referral_credit_bytes FROM users WHERE telegram_id=:id",
        {"id": query.from_user.id},
    )
    available_volume = DAILY_BASE_BYTES + int(
        (credit_row or {}).get("referral_credit_bytes") or 0
    )
    expiry = format_expiry(next_tehran_midnight(), "fa")
    body = (
        f"<b>کشور انتخابی: {escape(item['name_fa'])}</b>\n\n"
        f"حجم قابل دریافت امروز: <b>{format_bytes(available_volume)}</b>\n"
        f"زمان پایان: <b>{expiry}</b>\n"
        "تعداد دستگاه مجاز: <b>۲ دستگاه</b>\n\n"
        "با تأیید، سهمیه امروز شما روی همین کشور ساخته می‌شود."
    )
    rendered = await services.emoji.render_html(
        item["flag_emoji_key"], body, fallback=item.get("fallback_flag", "🌍")
    )
    await _edit_or_answer(
        query.message,
        rendered,
        reply_markup=await keyboards.confirm_location(services.emoji, code),
    )


@router.callback_query(F.data.startswith("private:create:"))
async def private_create(query: CallbackQuery, services: BotServices) -> None:
    if (
        query.from_user is None
        or not isinstance(query.message, Message)
        or not query.data
    ):
        await query.answer(
            "این پیام دیگر قابل استفاده نیست؛ /menu را ارسال کنید.",
            show_alert=True,
        )
        return
    code = query.data.rsplit(":", 1)[-1].upper()
    lock_key = f"bot:private-create:{query.from_user.id}"
    if not await services.redis.set(lock_key, "1", ex=20, nx=True):
        await query.answer("درخواست ساخت قبلی هنوز فعال است.", show_alert=True)
        return
    await query.answer("در حال ساخت اشتراک اختصاصی...")
    try:
        await services.subscriptions.ensure_user(
            query.from_user.id, query.from_user.username, "fa"
        )
        async with asyncio.timeout(15):
            sub = await services.subscriptions.create_daily_subscription(
                query.from_user.id, code
            )
    except (TimeoutError, CommunicationUnavailable, RuntimeError):
        logger.exception("private_subscription_create_failed")
        text = await services.messages.tester_timeout_message()
        await _edit_or_answer(
            query.message,
            text,
            reply_markup=await keyboards.confirm_location(services.emoji, code),
        )
        return
    except Exception:
        logger.exception("private_subscription_create_unexpected_failure")
        await _edit_or_answer(
            query.message,
            texts.GENERIC_ERROR,
            reply_markup=await keyboards.confirm_location(services.emoji, code),
        )
        return
    finally:
        await services.redis.delete(lock_key)
    url = str(sub.get("subscription_url") or "")
    if not url:
        await _edit_or_answer(
            query.message,
            texts.GENERIC_ERROR,
            reply_markup=await keyboards.back_menu(services.emoji),
        )
        return
    body = (
        "<b>اشتراک اختصاصی شما آماده است</b>\n\n"
        f"کشور: <b>{escape(str(sub.get('country_name_fa') or sub.get('location_code')))}</b>\n"
        "حجم: <b>"
        f"{format_volume_pair(sub.get('volume_used_bytes'), sub.get('volume_limit_bytes'))}"
        "</b>\n"
        f"انقضا: <b>{format_expiry(sub.get('expires_at'), 'fa')}</b>\n\n"
        f"<b>لینک اتصال:</b>\n<code>{escape(url)}</code>"
    )
    rendered = await services.emoji.render_html("success", body, fallback="✅")
    await _edit_or_answer(
        query.message,
        rendered,
        reply_markup=await keyboards.private_result(services.emoji, url),
    )


@router.callback_query(F.data == "account:home")
async def account_home(query: CallbackQuery, services: BotServices) -> None:
    await query.answer()
    if query.from_user is None or query.message is None:
        return
    sub = await services.subscriptions.get_active(query.from_user.id)
    if sub is None:
        body = (
            "<b>هنوز اشتراک اختصاصی فعالی ندارید.</b>\n\n"
            "از بخش اشتراک اختصاصی، کشور دلخواه را انتخاب کنید."
        )
        await _edit_or_answer(
            query.message,
            body,
            reply_markup=await keyboards.back_menu(services.emoji),
        )
        return
    url = str(sub.get("subscription_url") or "")
    body = (
        "<b>اشتراک اختصاصی فعال</b>\n\n"
        f"کشور: <b>{escape(str(sub.get('country_name_fa') or sub.get('location_code')))}</b>\n"
        "مصرف: <b>"
        f"{format_volume_pair(sub.get('volume_used_bytes'), sub.get('volume_limit_bytes'))}"
        "</b>\n"
        f"انقضا: <b>{format_expiry(sub.get('expires_at'), 'fa')}</b>\n\n"
        f"<b>لینک اتصال:</b>\n<code>{escape(url)}</code>"
    )
    rendered = await services.emoji.render_html("account", body, fallback="👤")
    await _edit_or_answer(
        query.message,
        rendered,
        reply_markup=(
            await keyboards.private_result(services.emoji, url)
            if url
            else await keyboards.back_menu(services.emoji)
        ),
    )


@router.callback_query(F.data == "referral:home")
async def referral_home(query: CallbackQuery, services: BotServices) -> None:
    await query.answer()
    if query.from_user is None or query.message is None:
        return
    me = await query.bot.get_me()
    row = await services.db.fetch_one(
        """
        SELECT referral_credit_bytes,
               (SELECT COUNT(*) FROM users r WHERE r.referred_by=u.telegram_id) AS referrals
        FROM users u WHERE telegram_id=:id
        """,
        {"id": query.from_user.id},
    )
    url = f"https://t.me/{me.username}?start=ref_{query.from_user.id}"
    body = (
        "<b>دعوت دوستان و دریافت حجم بیشتر</b>\n\n"
        "به‌ازای هر دوست جدید، ۵۱۲ مگابایت به سهمیه اختصاصی شما اضافه می‌شود.\n\n"
        f"تعداد دعوت موفق: <b>{int((row or {}).get('referrals') or 0)}</b>\n"
        f"اعتبار آماده: <b>{format_bytes((row or {}).get('referral_credit_bytes'))}</b>\n\n"
        f"<b>لینک دعوت:</b>\n<code>{escape(url)}</code>"
    )
    rendered = await services.emoji.render_html("referral", body, fallback="👥")
    await _edit_or_answer(
        query.message,
        rendered,
        reply_markup=await keyboards.back_menu(services.emoji),
    )


@router.callback_query(F.data == "report:start")
async def report_start(
    query: CallbackQuery, services: BotServices, state: FSMContext
) -> None:
    if not isinstance(query.message, Message):
        await query.answer(
            "این پیام دیگر قابل استفاده نیست؛ /menu را ارسال کنید.",
            show_alert=True,
        )
        return
    await query.answer()
    await state.set_state(ReportFlow.waiting_for_detail)
    rendered = await services.emoji.render_html(
        "report", texts.REPORT_PROMPT, fallback="🚨"
    )
    await _edit_or_answer(
        query.message,
        rendered,
        reply_markup=await keyboards.back_menu(services.emoji),
    )


@router.message(ReportFlow.waiting_for_detail, F.text)
async def report_detail(
    message: Message, services: BotServices, state: FSMContext
) -> None:
    if message.from_user is None or not message.text:
        return
    detail = message.text.strip()
    if not 8 <= len(detail) <= 500:
        await message.answer("توضیح مشکل باید بین ۸ تا ۵۰۰ نویسه باشد.")
        return
    inferred = await infer_operator_report(get_gateway(), detail)
    category = str(inferred.get("category") or "other")
    operator = str(inferred.get("operator") or "unknown")
    await services.db.execute(
        """
        INSERT INTO user_reports(user_id, config_id, category, detail)
        VALUES (:user_id, NULL, :category, :detail)
        """,
        {
            "user_id": message.from_user.id,
            "category": category if category in texts.REPORT_CATEGORIES else "other",
            "detail": str(inferred.get("summary") or detail)[:500],
        },
    )
    if operator in texts.OPERATOR_NAMES and operator != "unknown":
        await services.db.execute(
            "UPDATE users SET mobile_operator=:operator WHERE telegram_id=:id",
            {"operator": operator, "id": message.from_user.id},
        )
    await state.clear()
    await message.answer(
        "گزارش شما ثبت شد.\n"
        f"اپراتور: {texts.OPERATOR_NAMES.get(operator, 'نامشخص')}\n"
        f"نوع مشکل: {texts.REPORT_CATEGORIES.get(category, 'سایر')}",
        reply_markup=await keyboards.back_menu(services.emoji),
    )


@router.message(ReportFlow.waiting_for_detail)
async def report_non_text(message: Message) -> None:
    await message.answer("لطفاً مشکل را فقط به‌صورت پیام متنی ارسال کنید.")


@router.message(Command("emojiid"))
async def emoji_id(message: Message, services: BotServices) -> None:
    if message.from_user is None or message.from_user.id not in services.settings.admin_ids:
        return
    source = message.reply_to_message
    entities = (
        ((source.entities or source.caption_entities) if source else None)
        or message.entities
        or message.caption_entities
        or []
    )
    ids = [
        entity.custom_emoji_id
        for entity in entities
        if entity.type == MessageEntityType.CUSTOM_EMOJI and entity.custom_emoji_id
    ]
    if not ids:
        await message.answer(
            "روی یک پیام دارای Custom Emoji ریپلای کنید و /emojiid را بفرستید."
        )
        return
    await message.answer(
        "Custom Emoji ID:\n" + "\n".join(f"<code>{item}</code>" for item in ids),
        parse_mode="HTML",
    )


@router.message(Command("ask"))
async def admin_ask(message: Message, services: BotServices) -> None:
    if message.from_user is None or message.from_user.id not in services.settings.admin_ids:
        return
    question = (message.text or "").removeprefix("/ask").strip()
    if not question:
        await message.answer("نمونه استفاده: /ask وضعیت تستر چطور است؟")
        return
    healthy = await services.db.fetch_one(
        """
        SELECT COUNT(*) AS count FROM vpn_configs
        WHERE scope='public' AND is_enabled AND score>=50
        """
    )
    dead = await services.db.fetch_one(
        """
        SELECT COUNT(*) AS count FROM vpn_configs
        WHERE scope='public' AND (NOT is_enabled OR score<50)
        """
    )
    answer = await admin_assist(
        get_gateway(),
        question=question,
        snapshot={
            "healthy_configs": int((healthy or {}).get("count") or 0),
            "dead_configs": int((dead or {}).get("count") or 0),
            "ai_enabled": get_gateway().enabled,
        },
    )
    await message.answer(answer[:4000])


@router.errors()
async def on_error(event: ErrorEvent, services: BotServices) -> bool:
    logger.exception("bot_handler_error", exc_info=event.exception)
    message = event.update.message
    if message:
        await message.answer(texts.GENERIC_ERROR)
    elif event.update.callback_query:
        try:
            await event.update.callback_query.answer(
                texts.GENERIC_ERROR, show_alert=True
            )
        except TelegramBadRequest:
            pass
    return True
