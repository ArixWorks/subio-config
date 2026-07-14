import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.communication import CircuitBreaker, CommunicationUnavailable, ResilientTesterClient
from app.config import Settings, get_settings
from app.db import Database
from app.logging import configure_logging
from app.s3_transport import S3FallbackStore
from app.security import PayloadCipher, constant_time_token
from app.services.comm_service import CommunicationManager
from app.services.config_tester import ConfigTesterService
from app.services.distribution_service import DistributionService
from app.services.emoji_service import EmojiService
from app.services.message_service import MessageService
from app.services.panel_service import PanelService
from app.services.socks_service import SocksService
from app.services.scanner_settings_service import ScannerSettingsService
from app.services.subscription_service import SubscriptionService

logger = logging.getLogger("subio.api")
TEST_TIMEOUTS = Counter("subio_tester_timeout_total", "Tester operations that timed out")
REPORTS = Counter("subio_user_reports_total", "User reports received", ["category"])
STATIC_DIR = Path(__file__).resolve().parent / "admin" / "static"


class TestRequest(BaseModel):
    config_uri: str = Field(min_length=10, max_length=8192)
    protocol: str = Field(pattern=r"^(vless|vmess|trojan|ss|wireguard)$")


class ReportRequest(BaseModel):
    telegram_id: int
    config_id: UUID
    category: str = Field(pattern=r"^(blocked|slow|disconnect|other)$")
    detail: str | None = Field(default=None, max_length=1000)


class UIAsset(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    language: str = Field(default="fa", pattern=r"^[a-z]{2,5}$")
    value: str = Field(min_length=1, max_length=256)
    type: str = Field(default="emoji", pattern=r"^(emoji|custom_emoji|color|text)$")
    description: str | None = Field(default=None, max_length=500)


class PanelCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    base_url: str
    username: str
    password: str


class SocksCreate(BaseModel):
    name: str = ""
    host: str
    port: int = Field(ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    protocol: str = Field(default="socks5", pattern=r"^(socks4|socks5)$")
    priority: int = 0
    is_active: bool = True


class SocksUpdate(SocksCreate):
    pass


class SocksBulk(BaseModel):
    text: str = Field(min_length=1, max_length=50_000)


class SocksActive(BaseModel):
    is_active: bool


class CommForce(BaseModel):
    mode: str = Field(pattern=r"^(direct|arvan_s3)$")


class SystemMessageUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=2000)


class BroadcastRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)
    target: str = Field(default="all", pattern=r"^(all|active)$")


class ScannerSettingsUpdate(BaseModel):
    npv_to_v2ray: bool
    decrypt_bot: bool
    protocols: dict[str, bool]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.settings = settings
    app.state.db = Database(settings.database_url)
    app.state.redis = Redis.from_url(settings.redis_url, decode_responses=True)
    cipher = PayloadCipher(settings.payload_encryption_key)
    app.state.cipher = cipher
    fallback = None
    if settings.s3_enabled:
        fallback = S3FallbackStore(
            endpoint=settings.arvan_s3_endpoint or "",
            region=settings.arvan_s3_region,
            bucket=settings.arvan_s3_bucket or "",
            access_key=settings.arvan_s3_access_key or "",
            secret_key=settings.arvan_s3_secret_key or "",
        )
    breaker = CircuitBreaker(
        settings.breaker_failure_threshold,
        settings.breaker_recovery_successes,
        settings.breaker_reset_seconds,
    )
    app.state.tester = ResilientTesterClient(
        base_url=str(settings.tester_base_url),
        hmac_key=settings.internal_hmac_key,
        cipher=cipher,
        breaker=breaker,
        fallback=fallback,
        timeout=settings.tester_timeout_seconds,
    )
    app.state.comm = CommunicationManager(app.state.db, app.state.tester)
    app.state.messages = MessageService(app.state.db)
    app.state.emoji = EmojiService(app.state.db, app.state.redis)
    app.state.scanner_settings = ScannerSettingsService(app.state.db, app.state.redis)
    app.state.config_tester = ConfigTesterService(
        app.state.db, app.state.tester, cipher, app.state.scanner_settings
    )
    app.state.panels = PanelService(app.state.db, cipher)
    app.state.subscriptions = SubscriptionService(app.state.db, app.state.panels)
    app.state.distribution = DistributionService(app.state.db)
    app.state.socks = SocksService(
        app.state.db,
        cipher,
        str(settings.tester_base_url),
        hmac_key=settings.internal_hmac_key,
        payload_cipher=cipher,
    )
    app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    await app.state.comm.load()
    yield
    await app.state.arq.close()
    await app.state.redis.aclose()
    await app.state.db.close()


app = FastAPI(title="SubIO Main API", version="2.1.0", lifespan=lifespan)
if STATIC_DIR.exists():
    app.mount("/admin/static", StaticFiles(directory=STATIC_DIR), name="admin-static")


def settings(request: Request) -> Settings:
    return request.app.state.settings


async def require_admin(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    token = authorization.removeprefix("Bearer ") if authorization else None
    if not constant_time_token(token, request.app.state.settings.admin_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid admin credentials")


async def rate_limit(request: Request, bucket: str, limit: int = 10) -> None:
    remote = request.client.host if request.client else "unknown"
    window = int(time.time() // 60)
    key = f"rate:{bucket}:{remote}:{window}"
    count = await request.app.state.redis.incr(key)
    if count == 1:
        await request.app.state.redis.expire(key, 65)
    if count > limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    db_ok = await request.app.state.db.ready()
    redis_ok = bool(await request.app.state.redis.ping())
    if not db_ok or not redis_ok:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "dependencies unavailable")
    return {"status": "ready"}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin")
async def admin_panel() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "admin UI not built")
    return FileResponse(index)


@app.post("/v1/tests")
async def dispatch_test(body: TestRequest, request: Request) -> dict[str, Any]:
    await rate_limit(request, "test", 20)
    messages: MessageService = request.app.state.messages
    try:
        return await request.app.state.config_tester.test_and_store(
            config_id=None, uri=body.config_uri, protocol=body.protocol
        )
    except CommunicationUnavailable as exc:
        TEST_TIMEOUTS.inc()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, await messages.tester_timeout_message()) from exc


@app.post("/v1/reports", status_code=status.HTTP_202_ACCEPTED)
async def report(body: ReportRequest, request: Request) -> dict[str, str]:
    await rate_limit(request, "report", 5)
    await request.app.state.db.execute(
        """
        INSERT INTO user_reports(user_id, config_id, category, detail)
        VALUES (:user_id, :config_id, :category, :detail)
        """,
        {
            "user_id": body.telegram_id,
            "config_id": body.config_id,
            "category": body.category,
            "detail": body.detail,
        },
    )
    REPORTS.labels(category=body.category).inc()
    return {"status": "accepted"}


@app.get("/sub/{token}")
async def subscription(token: UUID, request: Request) -> Response:
    row = await request.app.state.db.fetch_one(
        """
        SELECT s.id FROM subscriptions s
        WHERE s.token=:token AND s.is_active AND s.expires_at > now()
          AND s.volume_used_bytes < s.volume_limit_bytes
        """,
        {"token": token},
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
    configs_rows: list[dict[str, Any]] = []
    async with request.app.state.db.engine.connect() as conn:
        from sqlalchemy import text

        result = await conn.execute(
            text(
                """
                SELECT uri_enc FROM vpn_configs
                WHERE is_enabled AND score >= 50
                  AND (scope='public' OR subscription_id=:subscription_id)
                  AND (expires_at IS NULL OR expires_at > now())
                ORDER BY score DESC LIMIT 10
                """
            ),
            {"subscription_id": row["id"]},
        )
        configs_rows = [dict(item) for item in result.mappings().all()]
    plaintext = [
        request.app.state.cipher.decrypt(str(item["uri_enc"]), aad=b"subio:config:v1")["uri"]
        for item in configs_rows
    ]
    return PlainTextResponse("\n".join(plaintext), headers={"Cache-Control": "no-store"})


@app.get("/admin/dashboard", dependencies=[Depends(require_admin)])
async def dashboard(request: Request) -> dict[str, Any]:
    users = await request.app.state.db.fetch_one("SELECT COUNT(*) AS c FROM users", {})
    configs = await request.app.state.db.fetch_one(
        "SELECT COUNT(*) AS c FROM vpn_configs WHERE is_enabled", {}
    )
    comm = await request.app.state.comm.load()
    return {
        "users": int(users["c"]) if users else 0,
        "configs": int(configs["c"]) if configs else 0,
        "comm_mode": comm.mode,
    }


@app.get("/admin/communication", dependencies=[Depends(require_admin)])
async def communication_state(request: Request) -> dict[str, Any]:
    comm: CommunicationManager = request.app.state.comm
    state = await comm.load()
    breaker = request.app.state.tester._breaker
    return {
        "mode": state.mode,
        "forced_mode": state.forced_mode,
        "breaker_state": breaker.state,
        "failures": breaker.failures,
        "recovery_successes": breaker.recovery_successes,
        "recent_switches": await comm.recent_switches(20),
    }


@app.post("/admin/communication/probe", dependencies=[Depends(require_admin)])
async def communication_probe(request: Request) -> dict[str, Any]:
    return await request.app.state.comm.probe_and_reconcile()


@app.post("/admin/communication/force", dependencies=[Depends(require_admin)])
async def communication_force(body: CommForce, request: Request) -> dict[str, str]:
    await request.app.state.comm.force_mode(body.mode)
    return {"status": "forced", "mode": body.mode}


@app.delete("/admin/communication/force", dependencies=[Depends(require_admin)])
async def communication_clear_force(request: Request) -> dict[str, str]:
    await request.app.state.comm.clear_force()
    return {"status": "cleared"}


@app.get("/admin/ui-assets", dependencies=[Depends(require_admin)])
async def list_assets(request: Request) -> list[dict[str, Any]]:
    async with request.app.state.db.engine.connect() as conn:
        from sqlalchemy import text

        rows = (
            await conn.execute(text("SELECT key, language, value, type, description FROM ui_assets ORDER BY key"))
        ).mappings().all()
    return [dict(row) for row in rows]


@app.put("/admin/ui-assets", dependencies=[Depends(require_admin)])
async def upsert_asset(body: UIAsset, request: Request) -> dict[str, str]:
    await rate_limit(request, "admin", 60)
    await request.app.state.db.execute(
        """
        INSERT INTO ui_assets(key, language, value, type, description)
        VALUES (:key,:language,:value,:type,:description)
        ON CONFLICT (key,language) DO UPDATE SET value=excluded.value,
          type=excluded.type, description=excluded.description, updated_at=now()
        """,
        body.model_dump(),
    )
    await request.app.state.emoji.invalidate(body.key, body.language)
    return {"status": "saved", "fingerprint": hashlib.sha256(body.value.encode()).hexdigest()[:12]}


@app.get("/admin/socks", dependencies=[Depends(require_admin)])
async def list_socks(request: Request) -> list[dict[str, Any]]:
    return await request.app.state.socks.list_proxies()


@app.post("/admin/socks", dependencies=[Depends(require_admin)])
async def create_socks(body: SocksCreate, request: Request) -> dict[str, Any]:
    payload = body.model_dump()
    if not payload.get("name"):
        payload["name"] = f"{payload['host']}:{payload['port']}"
    await request.app.state.socks.upsert(**payload)
    try:
        await request.app.state.socks.sync_to_tester()
    except Exception:
        logger.warning("socks_sync_failed_after_create")
    return {"status": "saved"}


@app.post("/admin/socks/bulk", dependencies=[Depends(require_admin)])
async def create_socks_bulk(body: SocksBulk, request: Request) -> dict[str, Any]:
    result = await request.app.state.socks.upsert_many_uris(body.text)
    try:
        await request.app.state.socks.sync_to_tester()
    except Exception:
        logger.warning("socks_sync_failed_after_bulk")
    return result


@app.put("/admin/socks/{proxy_id}", dependencies=[Depends(require_admin)])
async def update_socks(proxy_id: int, body: SocksUpdate, request: Request) -> dict[str, str]:
    payload = body.model_dump()
    if not payload.get("name"):
        payload["name"] = f"{payload['host']}:{payload['port']}"
    await request.app.state.socks.upsert(**payload, proxy_id=proxy_id)
    try:
        await request.app.state.socks.sync_to_tester()
    except Exception:
        logger.warning("socks_sync_failed_after_update")
    return {"status": "updated"}


@app.patch("/admin/socks/{proxy_id}/active", dependencies=[Depends(require_admin)])
async def toggle_socks(proxy_id: int, body: SocksActive, request: Request) -> dict[str, str]:
    await request.app.state.socks.set_active(proxy_id, body.is_active)
    try:
        await request.app.state.socks.sync_to_tester()
    except Exception:
        logger.warning("socks_sync_failed_after_toggle")
    return {"status": "ok"}


@app.delete("/admin/socks/{proxy_id}", dependencies=[Depends(require_admin)])
async def delete_socks(proxy_id: int, request: Request) -> dict[str, str]:
    await request.app.state.socks.delete(proxy_id)
    try:
        await request.app.state.socks.sync_to_tester()
    except Exception:
        logger.warning("socks_sync_failed_after_delete")
    return {"status": "deleted"}


@app.post("/admin/socks/check", dependencies=[Depends(require_admin)])
async def check_socks(request: Request) -> list[dict[str, Any]]:
    return await request.app.state.socks.trigger_health_check()


@app.post("/admin/socks/sync", dependencies=[Depends(require_admin)])
async def sync_socks(request: Request) -> dict[str, Any]:
    return await request.app.state.socks.sync_to_tester()


@app.get("/admin/panels", dependencies=[Depends(require_admin)])
async def list_panels(request: Request) -> list[dict[str, Any]]:
    async with request.app.state.db.engine.connect() as conn:
        from sqlalchemy import text

        rows = (await conn.execute(text("SELECT id, name, base_url, is_active FROM panels ORDER BY name"))).mappings().all()
    return [dict(row) for row in rows]


@app.post("/admin/panels", dependencies=[Depends(require_admin)])
async def create_panel(body: PanelCreate, request: Request) -> dict[str, str]:
    cipher: PayloadCipher = request.app.state.cipher
    await request.app.state.db.execute(
        """
        INSERT INTO panels(name, base_url, username_enc, password_enc)
        VALUES (:name, :base_url, :username_enc, :password_enc)
        ON CONFLICT (name) DO UPDATE SET base_url=excluded.base_url,
          username_enc=excluded.username_enc, password_enc=excluded.password_enc, is_active=TRUE
        """,
        {
            "name": body.name,
            "base_url": body.base_url.rstrip("/"),
            "username_enc": cipher.encrypt({"username": body.username}, aad=b"subio:panel:v1"),
            "password_enc": cipher.encrypt({"password": body.password}, aad=b"subio:panel:v1"),
        },
    )
    return {"status": "saved"}


@app.get("/admin/configs/public", dependencies=[Depends(require_admin)])
async def public_configs_admin(request: Request) -> list[dict[str, Any]]:
    return await request.app.state.distribution.top_configs()


@app.get("/admin/messages/{key}", dependencies=[Depends(require_admin)])
async def get_message(key: str, request: Request) -> dict[str, str]:
    value = await request.app.state.messages.get(key)
    return {"key": key, "value": value}


@app.put("/admin/messages/{key}", dependencies=[Depends(require_admin)])
async def update_message(key: str, body: SystemMessageUpdate, request: Request) -> dict[str, str]:
    await request.app.state.db.execute(
        """
        INSERT INTO system_messages(key, value) VALUES (:key, :value)
        ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=now()
        """,
        {"key": key, "value": body.value},
    )
    return {"status": "saved"}


@app.get("/admin/scanner-settings", dependencies=[Depends(require_admin)])
async def get_scanner_settings(request: Request) -> dict[str, Any]:
    settings = await request.app.state.scanner_settings.get()
    return settings.to_dict()


@app.put("/admin/scanner-settings", dependencies=[Depends(require_admin)])
async def update_scanner_settings(body: ScannerSettingsUpdate, request: Request) -> dict[str, Any]:
    await rate_limit(request, "admin", 60)
    if not any(body.protocols.values()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "حداقل یک پروتکل باید فعال باشد")
    updated = await request.app.state.scanner_settings.update(
        npv_to_v2ray=body.npv_to_v2ray,
        decrypt_bot=body.decrypt_bot,
        protocols=body.protocols,
    )
    return updated.to_dict()


@app.post("/admin/broadcasts", dependencies=[Depends(require_admin)])
async def create_broadcast(body: BroadcastRequest, request: Request) -> dict[str, str]:
    import uuid

    from app.ai.broadcast_polish import polish_broadcast
    from app.ai.gateway import get_gateway

    message = await polish_broadcast(get_gateway(), body.message)
    broadcast_id = uuid.uuid4()
    await request.app.state.db.execute(
        """
        INSERT INTO broadcasts(id, message, target, status)
        VALUES (:id, :message, :target, 'pending')
        """,
        {"id": broadcast_id, "message": message, "target": body.target},
    )
    await request.app.state.arq.enqueue_job("run_broadcast", str(broadcast_id))
    return {"status": "queued", "id": str(broadcast_id), "message": message}


class AIChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


class AIPolishRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)


@app.post("/admin/ai/chat", dependencies=[Depends(require_admin)])
async def admin_ai_chat(body: AIChatRequest, request: Request) -> dict[str, Any]:
    await rate_limit(request, "admin-ai", 30)
    from app.ai.admin_assistant import admin_assist
    from app.ai.gateway import get_gateway

    healthy = await request.app.state.db.fetch_one(
        "SELECT COUNT(*) AS c FROM vpn_configs WHERE scope='public' AND is_enabled AND score>=50"
    )
    dead = await request.app.state.db.fetch_one(
        "SELECT COUNT(*) AS c FROM vpn_configs WHERE scope='public' AND (NOT is_enabled OR score<50)"
    )
    advice = await request.app.state.redis.get("ai:scanner_advice")
    triage = await request.app.state.redis.get("ai:log_triage")
    snapshot = {
        "healthy": int((healthy or {}).get("c") or 0),
        "dead": int((dead or {}).get("c") or 0),
        "scanner_advice": advice,
        "log_triage": triage,
        "ai_enabled": get_gateway().enabled,
    }
    answer = await admin_assist(get_gateway(), question=body.question, snapshot=snapshot)
    return {"answer": answer, "snapshot": snapshot}


@app.post("/admin/ai/polish-broadcast", dependencies=[Depends(require_admin)])
async def admin_ai_polish(body: AIPolishRequest, request: Request) -> dict[str, str]:
    await rate_limit(request, "admin-ai", 30)
    from app.ai.broadcast_polish import polish_broadcast
    from app.ai.gateway import get_gateway

    return {"message": await polish_broadcast(get_gateway(), body.message)}


@app.get("/admin/ai/triage", dependencies=[Depends(require_admin)])
async def admin_ai_triage(request: Request) -> dict[str, Any]:
    raw = await request.app.state.redis.get("ai:log_triage")
    advice = await request.app.state.redis.get("ai:scanner_advice")
    return {"log_triage": raw, "scanner_advice": advice}


@app.get("/admin/ai/status", dependencies=[Depends(require_admin)])
async def admin_ai_status(request: Request) -> dict[str, Any]:
    from app.ai.gateway import get_gateway

    settings = request.app.state.settings
    return {
        "enabled": get_gateway().enabled,
        "model_sol": settings.ai_model_sol,
        "model_luna": settings.ai_model_luna,
        "retest": {
            "healthy_interval_s": settings.retest_healthy_interval_seconds,
            "healthy_batch": settings.retest_healthy_batch,
            "dead_interval_s": settings.retest_dead_interval_seconds,
            "dead_batch": settings.retest_dead_batch,
        },
    }
