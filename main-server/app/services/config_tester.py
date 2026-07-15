"""Config test orchestration, dual-pool retest, and Iran bandwidth-aware modes."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, Literal
from urllib.parse import urlsplit

from app.ai.gateway import get_gateway
from app.ai.naming import suggest_display_name
from app.communication import CommunicationUnavailable, ResilientTesterClient
from app.config import get_settings
from app.db import Database
from app.security import PayloadCipher
from app.services.scoring_service import compute_health_score, format_config_name
from app.services.scanner_settings_service import ScannerSettingsService

logger = logging.getLogger("subio.config_tester")

TestMode = Literal["cheap", "full"]


class ConfigTesterService:
    def __init__(
        self,
        db: Database,
        tester: ResilientTesterClient,
        cipher: PayloadCipher,
        scanner_settings: ScannerSettingsService | None = None,
    ) -> None:
        self._db = db
        self._tester = tester
        self._cipher = cipher
        self._scanner_settings = scanner_settings

    async def queue_untested_public_configs(self, limit: int = 20) -> int:
        if self._scanner_settings is None:
            enabled = frozenset({"vless", "vmess", "trojan", "ss", "wireguard"})
        else:
            enabled = (await self._scanner_settings.get()).protocols
        if not enabled:
            return 0
        protocol_list = ", ".join(f"'{p}'" for p in sorted(enabled))
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text(
                        f"""
                        SELECT id, protocol, uri_enc FROM vpn_configs
                        WHERE scope='public' AND tested_at IS NULL AND is_enabled
                          AND protocol IN ({protocol_list})
                        ORDER BY created_at ASC LIMIT :limit
                        """
                    ),
                    {"limit": limit},
                )
            ).mappings().all()
        queued = 0
        for row in rows:
            uri = self._cipher.decrypt(str(row["uri_enc"]), aad=b"subio:config:v1")["uri"]
            try:
                await self.test_and_store(
                    config_id=str(row["id"]),
                    uri=uri,
                    protocol=str(row["protocol"]),
                    mode="full",
                    purpose="discover",
                )
            except CommunicationUnavailable:
                # A single unreachable/slow config must never block the rest of
                # the discovery batch. Without this guard, one "poison-pill"
                # config sitting at the head of the untested queue (ordered by
                # created_at ASC) would deterministically re-trigger the same
                # tester timeout on every cron run, aborting the whole job and
                # starving every other pending config behind it indefinitely.
                # test_and_store already recorded the failed test_job before
                # raising, so we just log, skip, and keep draining the batch.
                logger.warning(
                    "queue_untested_public_configs_item_unavailable",
                    extra={"config_id": str(row["id"])},
                )
                continue
            queued += 1
        return queued

    async def retest_healthy_batch(self) -> dict[str, int]:
        """Round-robin cheap retest of healthy pool (~every 10s cadence via worker)."""
        import asyncio

        settings = get_settings()
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT id, protocol, uri_enc, score FROM vpn_configs
                        WHERE scope='public'
                          AND is_enabled = TRUE
                          AND tested_at IS NOT NULL
                          AND score >= 50
                        ORDER BY tested_at ASC NULLS FIRST
                        LIMIT :limit
                        """
                    ),
                    {"limit": settings.retest_healthy_batch},
                )
            ).mappings().all()

        sem = asyncio.Semaphore(3)
        ok = fail = 0

        async def _one(row: Any) -> bool:
            async with sem:
                uri = self._cipher.decrypt(str(row["uri_enc"]), aad=b"subio:config:v1")["uri"]
                result = await self.test_and_store(
                    config_id=str(row["id"]),
                    uri=uri,
                    protocol=str(row["protocol"]),
                    mode="cheap",
                    purpose="retest_healthy",
                )
                return bool(result.get("reachable"))

        results = await asyncio.gather(*[_one(row) for row in rows], return_exceptions=True)
        for item in results:
            if isinstance(item, CommunicationUnavailable):
                break
            if isinstance(item, Exception):
                logger.exception("retest_healthy_item_failed", exc_info=item)
                fail += 1
            elif item:
                ok += 1
            else:
                fail += 1
        return {"checked": ok + fail, "ok": ok, "failed": fail}

    async def retest_dead_batch(self) -> dict[str, int]:
        """Cheap probe of dead configs; promote only after full confirmation."""
        settings = get_settings()
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT id, protocol, uri_enc FROM vpn_configs
                        WHERE scope='public'
                          AND (
                            is_enabled = FALSE
                            OR (tested_at IS NOT NULL AND score < 50)
                          )
                          AND tested_at IS NOT NULL
                          AND (expires_at IS NULL OR expires_at > now())
                        ORDER BY tested_at ASC NULLS FIRST
                        LIMIT :limit
                        """
                    ),
                    {"limit": settings.retest_dead_batch},
                )
            ).mappings().all()
        revived = failed = cheap_ok = 0
        for row in rows:
            uri = self._cipher.decrypt(str(row["uri_enc"]), aad=b"subio:config:v1")["uri"]
            try:
                cheap = await self.test_and_store(
                    config_id=str(row["id"]),
                    uri=uri,
                    protocol=str(row["protocol"]),
                    mode="cheap",
                    purpose="retest_dead_cheap",
                )
            except CommunicationUnavailable:
                break
            if not cheap.get("reachable"):
                failed += 1
                continue
            cheap_ok += 1
            # Bandwidth: only promote candidates get expensive full test.
            try:
                full = await self.test_and_store(
                    config_id=str(row["id"]),
                    uri=uri,
                    protocol=str(row["protocol"]),
                    mode="full",
                    purpose="retest_dead_promote",
                )
            except CommunicationUnavailable:
                break
            if full.get("reachable") and float(full.get("score") or 0) >= 50:
                revived += 1
            else:
                failed += 1
        return {"checked": len(rows), "cheap_ok": cheap_ok, "revived": revived, "failed": failed}

    async def test_and_store(
        self,
        *,
        config_id: str | None,
        uri: str,
        protocol: str,
        mode: TestMode = "full",
        purpose: str = "manual",
    ) -> dict[str, Any]:
        settings = get_settings()
        job_id = uuid.uuid4()
        envelope = self._cipher.encrypt(
            {
                "job_id": str(job_id),
                "payload": {"config_uri": uri, "protocol": protocol, "mode": mode},
            }
        )
        await self._db.execute(
            """
            INSERT INTO test_jobs(id, config_id, payload_enc, status, transport)
            VALUES (:id, :config_id, :payload, 'pending', 'direct')
            """,
            {"id": job_id, "config_id": config_id, "payload": envelope},
        )
        try:
            result = await self._tester.test(
                {"config_uri": uri, "protocol": protocol, "mode": mode}
            )
        except CommunicationUnavailable:
            await self._db.execute(
                "UPDATE test_jobs SET status='failed', error_code='tester_unavailable' WHERE id=:id",
                {"id": job_id},
            )
            raise
        score = float(result.get("health_score") or compute_health_score(result))
        reachable = bool(result.get("reachable"))
        await self._db.execute(
            """
            INSERT INTO test_results(job_id, reachable, latency_ms, download_mbps, checks, health_score)
            VALUES (:job_id, :reachable, :latency, :speed, CAST(:checks AS jsonb), :score)
            ON CONFLICT (job_id) DO UPDATE SET
              reachable=excluded.reachable, latency_ms=excluded.latency_ms,
              download_mbps=excluded.download_mbps, checks=excluded.checks,
              health_score=excluded.health_score
            """,
            {
                "job_id": job_id,
                "reachable": reachable,
                "latency": result.get("latency_ms"),
                "speed": result.get("download_mbps"),
                "checks": json.dumps({**(result.get("checks") or {}), "mode": mode, "purpose": purpose}),
                "score": score,
            },
        )
        if config_id:
            await self._apply_pool_transition(
                config_id=config_id,
                uri=uri,
                protocol=protocol,
                result=result,
                score=score,
                reachable=reachable,
                mode=mode,
                purpose=purpose,
                demote_on_first_fail=settings.retest_demote_on_first_fail,
            )
        await self._db.execute(
            "UPDATE test_jobs SET status='completed', completed_at=now() WHERE id=:id",
            {"id": job_id},
        )
        return {"job_id": str(job_id), "score": score, "mode": mode, **result}

    async def _apply_pool_transition(
        self,
        *,
        config_id: str,
        uri: str,
        protocol: str,
        result: dict[str, Any],
        score: float,
        reachable: bool,
        mode: TestMode,
        purpose: str,
        demote_on_first_fail: bool,
    ) -> None:
        transport = self._detect_transport(uri)
        location = self._guess_location(uri)
        conf_num = int(hashlib.sha256(uri.encode()).hexdigest(), 16) % 10000
        display = format_config_name(
            config_id=conf_num,
            location=location,
            protocol=protocol,
            latency_ms=result.get("latency_ms"),
            score=score,
            transport=transport,
        )
        if mode == "full" and reachable and score >= 50:
            try:
                display = await suggest_display_name(
                    get_gateway(),
                    protocol=protocol,
                    transport=transport,
                    location=location,
                    latency_ms=result.get("latency_ms"),
                    score=score,
                    config_id=conf_num,
                )
            except Exception:
                logger.exception("ai_naming_failed")

        # Immediate demotion when healthy dies (Iran cheap fail).
        demote = (not reachable) and (
            demote_on_first_fail or purpose.startswith("retest_healthy")
        )
        promote = reachable and score >= 50 and mode == "full"

        if demote:
            await self._db.execute(
                """
                UPDATE vpn_configs
                SET score=:score,
                    latency_ms=:latency,
                    tested_at=now(),
                    consecutive_failures=consecutive_failures + 1,
                    is_enabled=FALSE,
                    transport_type=:transport,
                    display_name=COALESCE(display_name, :display)
                WHERE id=:id
                """,
                {
                    "score": min(score, 10.0),
                    "latency": result.get("latency_ms"),
                    "transport": transport,
                    "display": display,
                    "id": config_id,
                },
            )
            logger.info("config_demoted", extra={"id": config_id, "purpose": purpose, "mode": mode})
            return

        if promote:
            await self._db.execute(
                """
                UPDATE vpn_configs
                SET score=:score, latency_ms=:latency, tested_at=now(),
                    consecutive_failures=0, is_enabled=TRUE,
                    transport_type=:transport, display_name=:display
                WHERE id=:id
                """,
                {
                    "score": score,
                    "latency": result.get("latency_ms"),
                    "transport": transport,
                    "display": display,
                    "id": config_id,
                },
            )
            logger.info("config_promoted", extra={"id": config_id, "purpose": purpose})
            return

        # Cheap OK on already-healthy: refresh latency without rewriting speed semantics.
        await self._db.execute(
            """
            UPDATE vpn_configs
            SET score=CASE
                  WHEN :reachable AND :mode = 'cheap' THEN GREATEST(score, :score)
                  WHEN :reachable THEN :score
                  ELSE LEAST(score, :score)
                END,
                latency_ms=COALESCE(:latency, latency_ms),
                tested_at=now(),
                consecutive_failures=CASE WHEN :reachable THEN 0 ELSE consecutive_failures+1 END,
                is_enabled=CASE
                  WHEN NOT :reachable THEN FALSE
                  WHEN :score < 20 THEN FALSE
                  ELSE is_enabled
                END,
                transport_type=:transport,
                display_name=CASE WHEN :set_display THEN :display ELSE display_name END
            WHERE id=:id
            """,
            {
                "score": score,
                "latency": result.get("latency_ms"),
                "reachable": reachable,
                "mode": mode,
                "transport": transport,
                "display": display,
                "set_display": mode == "full" and reachable,
                "id": config_id,
            },
        )

    async def cleanup_dead_configs(self) -> int:
        async with self._db.connection() as conn:
            from sqlalchemy import text

            result = await conn.execute(
                text(
                    """
                    UPDATE vpn_configs
                    SET is_enabled=FALSE
                    WHERE scope='public'
                      AND (
                        consecutive_failures >= 5
                        OR (tested_at IS NOT NULL AND tested_at < now() - interval '7 days' AND score < 40)
                        OR (expires_at IS NOT NULL AND expires_at < now())
                      )
                    RETURNING id
                    """
                )
            )
            return len(result.fetchall())

    @staticmethod
    def _detect_transport(uri: str) -> str:
        query = urlsplit(uri).query.lower()
        if "security=reality" in query:
            return "Reality"
        if "security=tls" in query:
            return "TLS"
        if "type=ws" in query:
            return "WS"
        if "type=grpc" in query:
            return "GRPC"
        return urlsplit(uri).scheme.upper()

    @staticmethod
    def _guess_location(uri: str) -> str:
        host = (urlsplit(uri).hostname or "").lower()
        if any(token in host for token in ("us", "usa", "america")):
            return "US"
        if any(token in host for token in ("tr", "tur", "istanbul")):
            return "TR"
        if any(token in host for token in ("de", "ger", "frankfurt")):
            return "DE"
        return "DE"
