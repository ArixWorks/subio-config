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
                pushed += 1
            except Exception:
                logger.exception("sub_sync_failed", extra={"token": feed["token"]})
                failed += 1
        return {"pushed": pushed, "failed": failed, "total": len(feeds)}

    async def _build_feeds(self, *, limit: int) -> list[dict[str, Any]]:
        feeds: list[dict[str, Any]] = []
        async with self._db.engine.connect() as conn:
            subs = (
                await conn.execute(
                    text(
                        """
                        SELECT s.id, s.token::text AS token, s.expires_at
                        FROM subscriptions s
                        WHERE s.is_active AND s.expires_at > now()
                          AND s.volume_used_bytes < s.volume_limit_bytes
                        ORDER BY s.created_at DESC
                        LIMIT :limit
                        """
                    ),
                    {"limit": limit},
                )
            ).mappings().all()

            for sub in subs:
                rows = (
                    await conn.execute(
                        text(
                            """
                            SELECT uri_enc FROM vpn_configs
                            WHERE is_enabled AND score >= 50
                              AND (scope='public' OR subscription_id=:subscription_id)
                              AND (expires_at IS NULL OR expires_at > now())
                            ORDER BY score DESC LIMIT 10
                            """
                        ),
                        {"subscription_id": sub["id"]},
                    )
                ).mappings().all()
                configs: list[str] = []
                for row in rows:
                    try:
                        configs.append(
                            self._cipher.decrypt(str(row["uri_enc"]), aad=b"subio:config:v1")["uri"]
                        )
                    except Exception:
                        continue
                feeds.append(
                    {
                        "token": str(sub["token"]),
                        "configs": configs,
                        "expires_at": sub["expires_at"].isoformat() if sub["expires_at"] else None,
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
        async with self._s3._session.client("s3", **self._s3._client_options) as client:
            await client.put_object(
                Bucket=self._s3._bucket,
                Key=key,
                Body=envelope.encode(),
                ContentType="application/octet-stream",
                ServerSideEncryption="AES256",
                Metadata={"token": token, "synced-at": str(int(time.time()))},
            )
