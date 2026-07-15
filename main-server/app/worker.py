from typing import Any

from aiogram import Bot
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import text

from app.ai.daily_digest import daily_digest
from app.ai.gateway import get_gateway
from app.ai.log_triage import triage_logs
from app.ai.scanner_advisor import recommend_scanner_settings
from app.communication import CircuitBreaker, ResilientTesterClient
from app.config import get_settings
from app.db import Database
from app.logging import configure_logging
from app.s3_transport import S3FallbackStore
from app.security import PayloadCipher
from app.services.broadcast_service import BroadcastService
from app.services.comm_service import CommunicationManager
from app.services.config_tester import ConfigTesterService
from app.services.distribution_service import DistributionService
from app.services.forced_channel_service import ForcedChannelService
from app.services.panel_service import PanelService
from app.services.pipeline_events import PipelineEventService
from app.services.socks_service import SocksService
from app.services.scanner_settings_service import ScannerSettingsService
from app.services.subscription_service import SubscriptionService
from app.services.subscription_sync import SubscriptionSyncService
from redis.asyncio import Redis


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    db = Database(settings.database_url)
    cipher = PayloadCipher(settings.payload_encryption_key)
    fallback = None
    if settings.s3_enabled:
        fallback = S3FallbackStore(
            endpoint=settings.arvan_s3_endpoint or "",
            region=settings.arvan_s3_region,
            bucket=settings.arvan_s3_bucket or "",
            access_key=settings.arvan_s3_access_key or "",
            secret_key=settings.arvan_s3_secret_key or "",
        )
    tester = ResilientTesterClient(
        base_url=str(settings.tester_base_url),
        hmac_key=settings.internal_hmac_key,
        cipher=cipher,
        breaker=CircuitBreaker(
            settings.breaker_failure_threshold,
            settings.breaker_recovery_successes,
            settings.breaker_reset_seconds,
        ),
        fallback=fallback,
        timeout=settings.tester_timeout_seconds,
    )
    ctx["settings"] = settings
    ctx["db"] = db
    ctx["tester"] = tester
    ctx["comm"] = CommunicationManager(db, tester)
    ctx["redis"] = Redis.from_url(settings.redis_url, decode_responses=True)
    ctx["scanner_settings"] = ScannerSettingsService(db, ctx["redis"])
    ctx["pipeline_events"] = PipelineEventService(db)
    ctx["config_tester"] = ConfigTesterService(
        db, tester, cipher, ctx["scanner_settings"], pipeline_events=ctx["pipeline_events"]
    )
    ctx["distribution"] = DistributionService(db)
    ctx["panels"] = PanelService(db, cipher)
    ctx["channels"] = ForcedChannelService(db, ctx["redis"], ctx["panels"])
    ctx["subscriptions"] = SubscriptionService(db, ctx["panels"])
    ctx["socks"] = SocksService(
        db,
        cipher,
        str(settings.tester_base_url),
        hmac_key=settings.internal_hmac_key,
        payload_cipher=cipher,
    )
    ctx["broadcasts"] = BroadcastService(db, settings.bot_token)
    ctx["sub_sync"] = SubscriptionSyncService(
        db,
        cipher,
        tester_base_url=str(settings.tester_base_url),
        hmac_key=settings.internal_hmac_key,
        s3=fallback,
    )
    ctx["ai"] = get_gateway()


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["redis"].aclose()
    await ctx["db"].close()


async def cleanup(ctx: dict[str, Any]) -> None:
    db: Database = ctx["db"]
    async with db.connection() as connection:
        await connection.execute(
            text(
                """
                UPDATE subscriptions SET is_active=FALSE
                WHERE is_active AND (expires_at <= now() OR volume_used_bytes >= volume_limit_bytes)
                """
            )
        )
        await connection.execute(
            text(
                """
                DELETE FROM test_jobs
                WHERE created_at < now() - interval '24 hours' AND status IN ('pending','failed')
                """
            )
        )
        await connection.execute(
            text("DELETE FROM comm_switch_logs WHERE created_at < now() - interval '90 days'")
        )
        await connection.execute(
            text("DELETE FROM pipeline_events WHERE created_at < now() - interval '14 days'")
        )


async def comm_probe(ctx: dict[str, Any]) -> None:
    await ctx["comm"].probe_and_reconcile()


async def test_public_configs(ctx: dict[str, Any]) -> None:
    await ctx["config_tester"].queue_untested_public_configs(limit=15)


async def retest_healthy(ctx: dict[str, Any]) -> dict[str, int]:
    return await ctx["config_tester"].retest_healthy_batch()


async def retest_dead(ctx: dict[str, Any]) -> dict[str, int]:
    return await ctx["config_tester"].retest_dead_batch()


async def cleanup_configs(ctx: dict[str, Any]) -> None:
    await ctx["config_tester"].cleanup_dead_configs()


async def refresh_distribution(ctx: dict[str, Any]) -> None:
    await ctx["distribution"].update_display_names()


async def socks_health(ctx: dict[str, Any]) -> None:
    await ctx["socks"].trigger_health_check()


async def reset_daily_volumes(ctx: dict[str, Any]) -> None:
    await ctx["subscriptions"].reset_daily_volumes()


async def sync_panel_usage(ctx: dict[str, Any]) -> dict[str, int]:
    return await ctx["panels"].sync_usage()


async def revoke_inactive_panel_clients(ctx: dict[str, Any]) -> dict[str, int]:
    return await ctx["panels"].revoke_inactive_clients()


async def cleanup_orphaned_panel_clients(ctx: dict[str, Any]) -> dict[str, int]:
    return await ctx["panels"].process_cleanup_jobs()


async def process_broadcasts(ctx: dict[str, Any]) -> None:
    await ctx["broadcasts"].process_pending(limit=1)


async def run_broadcast(ctx: dict[str, Any], broadcast_id: str) -> dict[str, Any]:
    return await ctx["broadcasts"].run_broadcast(broadcast_id)


async def enforce_forced_channels(ctx: dict[str, Any]) -> None:
    bot = Bot(ctx["settings"].bot_token)
    try:
        deactivated = await ctx["channels"].enforce_active_subscribers(bot)
        if deactivated:
            await ctx["sub_sync"].sync_all(limit=300)
    finally:
        await bot.session.close()


async def sync_subscriptions_to_iran(ctx: dict[str, Any]) -> None:
    await ctx["sub_sync"].sync_all(limit=300)


async def _collect_daily_stats(db: Database) -> dict[str, Any]:
    healthy = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM vpn_configs WHERE scope='public' AND is_enabled AND score>=50"
    )
    dead = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM vpn_configs WHERE scope='public' AND (NOT is_enabled OR score<50)"
    )
    tests = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM test_jobs WHERE created_at >= date_trunc('day', now())"
    )
    users = await db.fetch_one("SELECT COUNT(*) AS c FROM subscriptions WHERE is_active")
    reports = await db.fetch_one(
        "SELECT COUNT(*) AS c FROM user_reports WHERE created_at >= date_trunc('day', now())"
    )
    return {
        "healthy": int((healthy or {}).get("c") or 0),
        "dead": int((dead or {}).get("c") or 0),
        "tests_today": int((tests or {}).get("c") or 0),
        "active_users": int((users or {}).get("c") or 0),
        "reports_today": int((reports or {}).get("c") or 0),
    }


async def send_daily_digest(ctx: dict[str, Any]) -> dict[str, Any]:
    stats = await _collect_daily_stats(ctx["db"])
    text = await daily_digest(ctx["ai"], stats)
    settings = ctx["settings"]
    bot = Bot(settings.bot_token)
    sent = 0
    try:
        for admin_id in settings.admin_ids:
            await bot.send_message(admin_id, text)
            sent += 1
    finally:
        await bot.session.close()
    return {"sent": sent, "stats": stats}


async def scanner_advisor_job(ctx: dict[str, Any]) -> dict[str, Any]:
    current = (await ctx["scanner_settings"].get()).to_dict()
    metrics_row = await ctx["db"].fetch_one(
        """
        SELECT
          COUNT(*) FILTER (WHERE status='failed' AND error_code LIKE '%decrypt%') AS decrypt_failures,
          COUNT(*) FILTER (WHERE status='completed') AS decrypt_successes
        FROM test_jobs
        WHERE created_at > now() - interval '24 hours'
        """
    )
    metrics = {
        "decrypt_failures": int((metrics_row or {}).get("decrypt_failures") or 0),
        "decrypt_successes": int((metrics_row or {}).get("decrypt_successes") or 0),
    }
    advice = await recommend_scanner_settings(ctx["ai"], current=current, metrics=metrics)
    if advice.get("priority") in {"medium", "high"} and advice.get("summary"):
        bot = Bot(ctx["settings"].bot_token)
        try:
            for admin_id in ctx["settings"].admin_ids:
                await bot.send_message(admin_id, f"🤖 پیشنهاد اسکنر:\n{advice['summary']}")
        finally:
            await bot.session.close()
    await ctx["redis"].set("ai:scanner_advice", str(advice), ex=86400)
    return advice


async def log_triage_job(ctx: dict[str, Any]) -> dict[str, Any]:
    rows = await ctx["db"].fetch_all(
        """
        SELECT error_code, COUNT(*) AS c
        FROM test_jobs
        WHERE status='failed' AND created_at > now() - interval '6 hours'
        GROUP BY error_code
        ORDER BY c DESC
        LIMIT 15
        """
    )
    if not rows:
        return {"skipped": True}
    blob = "\n".join(f"{r['error_code']}: {r['c']}" for r in rows)
    triage = await triage_logs(ctx["ai"], blob)
    await ctx["redis"].set("ai:log_triage", str(triage), ex=21600)
    if triage.get("severity") in {"high", "critical"}:
        bot = Bot(ctx["settings"].bot_token)
        try:
            msg = f"🚨 Triage: {triage.get('headline')}\nعلت: {triage.get('likely_cause')}"
            for admin_id in ctx["settings"].admin_ids:
                await bot.send_message(admin_id, msg)
        finally:
            await bot.session.close()
    return triage


class WorkerSettings:
    functions = [
        cleanup,
        comm_probe,
        test_public_configs,
        retest_healthy,
        retest_dead,
        cleanup_configs,
        refresh_distribution,
        socks_health,
        reset_daily_volumes,
        sync_panel_usage,
        revoke_inactive_panel_clients,
        process_broadcasts,
        run_broadcast,
        enforce_forced_channels,
        sync_subscriptions_to_iran,
        send_daily_digest,
        scanner_advisor_job,
        log_triage_job,
    ]
    on_startup = startup
    on_shutdown = shutdown
    cron_jobs = [
        cron(cleanup, minute=set(range(0, 60))),
        cron(comm_probe, minute=set(range(0, 60, 1))),
        cron(test_public_configs, minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57}),
        # Healthy retest every 10s (round-robin batch) — cheap mode on Iran.
        cron(retest_healthy, second={0, 10, 20, 30, 40, 50}),
        # Dead revival less frequent to save Iran download quota.
        cron(retest_dead, minute=set(range(0, 60, 3)), second=5),
        cron(cleanup_configs, hour={3}, minute=0),
        cron(refresh_distribution, minute={5, 20, 35, 50}),
        cron(socks_health, minute={10, 40}),
        cron(reset_daily_volumes, minute={2, 17, 32, 47}),
        cron(sync_panel_usage, minute=set(range(0, 60, 5)), second=20),
        cron(revoke_inactive_panel_clients, minute=set(range(0, 60)), second=35),
        cron(cleanup_orphaned_panel_clients, minute=set(range(0, 60)), second=50),
        cron(process_broadcasts, minute=set(range(0, 60, 2))),
        cron(enforce_forced_channels, minute={0, 30}),
        cron(sync_subscriptions_to_iran, minute={1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56}),
        cron(send_daily_digest, hour=7, minute=30),
        cron(scanner_advisor_job, hour={9, 21}, minute=15),
        cron(log_triage_job, minute={20, 50}),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 20
    job_timeout = 300
