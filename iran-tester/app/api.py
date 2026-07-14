import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

from app.config import get_settings
from app.logging import configure_logging
from app.s3_worker import S3Worker
from app.security import PayloadCipher, ReplayGuard, verify_signature
from app.socks import ProxyPool
from app.sub_store import SubscriptionStore, TOKEN_PATTERN
from app.xray import UnsupportedConfiguration, run_test

logger = logging.getLogger("subio.tester")
JOBS = Counter("subio_tester_jobs_total", "Tester jobs", ["outcome"])
DURATION = Histogram("subio_tester_job_seconds", "Tester job duration")
SELF_HEALTH = Gauge("subio_tester_self_healthy", "Tester readiness")
SUB_HITS = Counter("subio_subscription_hits_total", "Subscription edge hits", ["outcome"])


class EnvelopeRequest(BaseModel):
    envelope: str = Field(min_length=40, max_length=200_000)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.settings = settings
    app.state.cipher = PayloadCipher(settings.payload_encryption_key)
    app.state.replay = ReplayGuard()
    app.state.proxies = ProxyPool(settings.proxy_uris)
    app.state.sub_store = SubscriptionStore(settings.subscription_store_dir)
    s3_task = None
    if settings.arvan_s3_endpoint:
        worker = S3Worker(
            endpoint=settings.arvan_s3_endpoint,
            region=settings.arvan_s3_region,
            bucket=settings.arvan_s3_bucket or "",
            access_key=settings.arvan_s3_access_key or "",
            secret_key=settings.arvan_s3_secret_key or "",
            cipher=app.state.cipher,
            xray_binary=settings.xray_binary,
            test_url=settings.xray_test_url,
            poll_seconds=settings.s3_poll_seconds,
            operation_timeout=settings.max_operation_seconds,
            sub_store=app.state.sub_store,
        )
        s3_task = asyncio.create_task(worker.run(), name="s3-poller")
    SELF_HEALTH.set(1 if os.path.isfile(settings.xray_binary) else 0)
    yield
    if s3_task:
        s3_task.cancel()
        try:
            await s3_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="SubIO Iran Tester", version="2.1.0", lifespan=lifespan)


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(request: Request) -> dict[str, Any]:
    binary_ok = os.path.isfile(request.app.state.settings.xray_binary)
    if not binary_ok:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "xray unavailable")
    return {"status": "ready", "proxy_count": len(request.app.state.proxies.status())}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/socks/health")
async def socks_health(request: Request) -> list[dict[str, object]]:
    return request.app.state.proxies.status()


@app.post("/v1/socks/check")
async def socks_check(request: Request) -> list[dict[str, object]]:
    return await request.app.state.proxies.check_all("https://api.telegram.org")


@app.post("/v1/socks/reload")
async def socks_reload(
    body: EnvelopeRequest,
    request: Request,
    timestamp: Annotated[str, Header(alias="X-SubIO-Timestamp")],
    nonce: Annotated[str, Header(alias="X-SubIO-Nonce")],
    signature: Annotated[str, Header(alias="X-SubIO-Signature")],
) -> dict[str, Any]:
    settings = request.app.state.settings
    if not verify_signature(
        envelope=body.envelope,
        timestamp=timestamp,
        nonce=nonce,
        signature=signature,
        key=settings.internal_hmac_key,
    ) or not request.app.state.replay.accept(timestamp, nonce):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid request authentication")
    decrypted = request.app.state.cipher.decrypt(body.envelope)
    payload = decrypted.get("payload") or {}
    if payload.get("type") != "socks_reload":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid payload")
    proxies = payload.get("proxies") or []
    if not isinstance(proxies, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "proxies must be a list")
    clean = [str(item) for item in proxies if isinstance(item, str) and item.strip()]
    request.app.state.proxies.reload(clean)
    return {"status": "reloaded", "count": len(clean)}


@app.post("/v1/subscription-sync")
async def subscription_sync(
    body: EnvelopeRequest,
    request: Request,
    timestamp: Annotated[str, Header(alias="X-SubIO-Timestamp")],
    nonce: Annotated[str, Header(alias="X-SubIO-Nonce")],
    signature: Annotated[str, Header(alias="X-SubIO-Signature")],
) -> dict[str, str]:
    settings = request.app.state.settings
    if not verify_signature(
        envelope=body.envelope,
        timestamp=timestamp,
        nonce=nonce,
        signature=signature,
        key=settings.internal_hmac_key,
    ) or not request.app.state.replay.accept(timestamp, nonce):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid request authentication")
    decrypted = request.app.state.cipher.decrypt(body.envelope)
    payload = decrypted.get("payload") or {}
    if payload.get("type") != "subscription_sync":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid sync payload")
    token = str(payload.get("token") or "")
    configs = payload.get("configs") or []
    if not TOKEN_PATTERN.match(token) or not isinstance(configs, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid token/configs")
    request.app.state.sub_store.upsert(
        token,
        [str(item) for item in configs],
        expires_at=payload.get("expires_at"),
    )
    return {"status": "synced", "token": token}


@app.get("/sub/{token}")
async def subscription_edge(token: str, request: Request) -> Response:
    """Public subscription endpoint for users inside Iran (config.ir)."""
    if not TOKEN_PATTERN.match(token):
        SUB_HITS.labels(outcome="invalid").inc()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    body = request.app.state.sub_store.get_body(token)
    if body is None:
        SUB_HITS.labels(outcome="miss").inc()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
    SUB_HITS.labels(outcome="hit").inc()
    return PlainTextResponse(
        body,
        headers={
            "Cache-Control": "no-store",
            "Profile-Update-Interval": "12",
            "Subscription-Userinfo": "upload=0; download=0; total=0; expire=0",
        },
    )


@app.post("/v1/tests")
async def test(
    body: EnvelopeRequest,
    request: Request,
    timestamp: Annotated[str, Header(alias="X-SubIO-Timestamp")],
    nonce: Annotated[str, Header(alias="X-SubIO-Nonce")],
    signature: Annotated[str, Header(alias="X-SubIO-Signature")],
) -> dict[str, str]:
    settings = request.app.state.settings
    if not verify_signature(
        envelope=body.envelope,
        timestamp=timestamp,
        nonce=nonce,
        signature=signature,
        key=settings.internal_hmac_key,
    ) or not request.app.state.replay.accept(timestamp, nonce):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid request authentication")
    try:
        decrypted = request.app.state.cipher.decrypt(body.envelope)
        payload = decrypted["payload"]
        if not isinstance(payload, dict) or not isinstance(payload.get("config_uri"), str):
            raise ValueError("missing configuration")
        mode = str(payload.get("mode") or "full")
        if mode not in {"cheap", "full"}:
            mode = "full"
        with DURATION.time():
            async with asyncio.timeout(settings.max_operation_seconds):
                result = await run_test(
                    payload["config_uri"],
                    settings.xray_binary,
                    settings.xray_test_url,
                    timeout=min(8, settings.max_operation_seconds - 1),
                    mode=mode,
                )
        result["job_id"] = decrypted["job_id"]
        JOBS.labels(outcome="success").inc()
        return {"envelope": request.app.state.cipher.encrypt(result)}
    except (ValueError, UnsupportedConfiguration) as exc:
        JOBS.labels(outcome="invalid").inc()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unsupported configuration") from exc
    except TimeoutError as exc:
        JOBS.labels(outcome="timeout").inc()
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "test exceeded deadline") from exc
    except Exception as exc:
        JOBS.labels(outcome="failed").inc()
        logger.exception("test_failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "configuration test failed") from exc
