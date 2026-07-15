"""Push subscription feeds from Main to Iran edge (Direct + S3)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import text

from app.db import Database
from app.s3_transport import S3FallbackStore
from app.security import PayloadCipher, signed_headers
from app.services.scoring_service import with_display_name

logger = logging.getLogger("subio.sub_sync")


class SubscriptionSyncService:
    def __init__(
        self,
        db: Database,
        cipher: PayloadCipher,
        *,
        tester_base_url: str,
        hmac_key: str,
        s3: S3FallbackStore | None = None,
    ) -> None:
        self._db = db
        self._cipher = cipher
        self._base = tester_base_url.rstrip("/")
        self._hmac = hmac_key
        self._s3 = s3

    async def sync_all(self, limit: int = 200) -> dict[str, int]:
        feeds = await self._build_feeds(limit=limit)
        pushed = 0
        failed = 0
        for feed in feeds:
            try:
                await self._push(feed)
                await self._db.execute(
                    "UPDATE public_feeds SET updated_at=now(), last_config_ids=:ids WHERE token=:token",
                    {"token": feed["token"], "ids": feed.get("config_ids") or []},
                )
                pushed += 1
            except Exception:
                logger.exception("sub_sync_failed", extra={"token": feed["token"]})
                failed += 1
        return {"pushed": pushed, "failed": failed, "total": len(feeds)}

    async def sync_token(self, token: str) -> None:
        row = await self._db.fetch_one(
            """
            SELECT token::text AS token, is_active, operator_code,
                   COALESCE(excluded_config_ids, ARRAY[]::UUID[]) AS excluded_config_ids
            FROM public_feeds WHERE token=:token
            """,
            {"token": token},
        )
        if row is None:
            raise RuntimeError("public feed not found")
        configs: list[str] = []
        selected_ids: list[str] = []
        if bool(row["is_active"]):
            selected_ids, configs = await self._select_configs_for_feed(
                operator_code=row.get("operator_code"),
                excluded_config_ids=list(row.get("excluded_config_ids") or []),
            )
        feed = {"token": token, "configs": configs, "expires_at": None}
        await self._push(feed)
        await self._db.execute(
            "UPDATE public_feeds SET updated_at=now(), last_config_ids=:ids WHERE token=:token",
            {"token": token, "ids": selected_ids},
        )

    async def _select_configs_for_feed(
        self,
        *,
        operator_code: str | None,
        excluded_config_ids: list[Any],
    ) -> tuple[list[str], list[str]]:
        """Selects the top-N healthy, unblocked configs for a single feed,
        applying both the per-operator exclusion list (configs voted "bad"
        by >=5 distinct users on this same carrier) and the per-feed
        exclusion list (configs this specific user reported, which are
        force-replaced regardless of the operator-wide vote count).

        Returns (selected_config_ids, plaintext_uris_with_display_names).
        """
        rows = await self._db.fetch_all(
            """
            SELECT c.id::text AS id, c.uri_enc, c.display_name
            FROM vpn_configs c
            WHERE c.is_enabled AND c.score >= 50 AND c.scope='public'
              AND NOT c.is_globally_blocked
              AND (c.expires_at IS NULL OR c.expires_at > now())
              AND c.id != ALL(CAST(:excluded AS uuid[]))
              AND NOT EXISTS (
                SELECT 1 FROM config_operator_exclusions e
                WHERE e.config_id = c.id AND e.operator_code = :operator
              )
            ORDER BY c.score DESC, c.latency_ms ASC NULLS LAST
            LIMIT 10
            """,
            {
                "excluded": excluded_config_ids or [],
                "operator": operator_code or "__none__",
            },
        )
        selected_ids: list[str] = []
        configs: list[str] = []
        for row in rows:
            try:
                uri = self._cipher.decrypt(str(row["uri_enc"]), aad=b"subio:config:v1")["uri"]
            except Exception:
                continue
            display_name = row.get("display_name")
            if display_name:
                uri = with_display_name(uri, str(display_name))
            configs.append(uri)
            selected_ids.append(str(row["id"]))
        return selected_ids, configs

    async def _build_feeds(self, *, limit: int) -> list[dict[str, Any]]:
        feeds: list[dict[str, Any]] = []
        async with self._db.engine.connect() as conn:
            public_feeds = (
                await conn.execute(
                    text(
                        """
                        SELECT token::text AS token, is_active, operator_code,
                               COALESCE(excluded_config_ids, ARRAY[]::UUID[]) AS excluded_config_ids
                        FROM public_feeds
                        ORDER BY updated_at ASC
                        LIMIT :limit
                        """
                    ),
                    {"limit": limit},
                )
            ).mappings().all()

        for feed in public_feeds:
            selected_ids: list[str] = []
            configs: list[str] = []
            if bool(feed["is_active"]):
                selected_ids, configs = await self._select_configs_for_feed(
                    operator_code=feed.get("operator_code"),
                    excluded_config_ids=list(feed.get("excluded_config_ids") or []),
                )
            feeds.append(
                {
                    "token": str(feed["token"]),
                    "configs": configs,
                    "config_ids": selected_ids,
                    "expires_at": None,
                }
            )
        return feeds

    async def _push(self, feed: dict[str, Any]) -> None:
        envelope = self._cipher.encrypt(
            {
                "job_id": str(uuid.uuid4()),
                "payload": {
                    "type": "subscription_sync",
                    "token": feed["token"],
                    "configs": feed["configs"],
                    "expires_at": feed.get("expires_at"),
                },
            }
        )
        headers = signed_headers(envelope, self._hmac)
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.post(
                    f"{self._base}/v1/subscription-sync",
                    json={"envelope": envelope},
                    headers=headers,
                )
                response.raise_for_status()
            return
        except httpx.HTTPError:
            if self._s3 is None:
                raise
            await self._push_s3(feed["token"], envelope)

    async def _push_s3(self, token: str, envelope: str) -> None:
        assert self._s3 is not None
        key = f"subs/{token}.enc"
        # NOTE: ArvanCloud's S3-compatible storage rejects the
        # ServerSideEncryption parameter with HTTP 400 InvalidArgument. The
        # payload is already end-to-end encrypted via PayloadCipher before it
        # reaches this transport, so SSE-S3 is not required.
        async with self._s3._session.client("s3", **self._s3._client_options) as client:
            await client.put_object(
                Bucket=self._s3._bucket,
                Key=key,
                Body=envelope.encode(),
                ContentType="application/octet-stream",
                Metadata={"token": token, "synced-at": str(int(time.time()))},
            )
