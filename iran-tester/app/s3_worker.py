"""Poll Arvan S3 for subscription feed updates when Direct is unavailable."""

from __future__ import annotations

import logging
from typing import Any

import aioboto3
from botocore.config import Config

from app.security import PayloadCipher
from app.sub_store import SubscriptionStore
from app.xray import run_test

logger = logging.getLogger("subio.tester.s3")


class S3Worker:
    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        cipher: PayloadCipher,
        xray_binary: str,
        test_url: str,
        poll_seconds: int,
        operation_timeout: float,
        sub_store: SubscriptionStore | None = None,
    ) -> None:
        self._bucket = bucket
        self._cipher = cipher
        self._xray_binary = xray_binary
        self._test_url = test_url
        self._poll_seconds = poll_seconds
        self._operation_timeout = operation_timeout
        self._sub_store = sub_store
        self._session = aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        self._options: dict[str, Any] = {
            "endpoint_url": endpoint,
            "config": Config(
                signature_version="s3v4",
                retries={"max_attempts": 2, "mode": "standard"},
                connect_timeout=2,
                read_timeout=2,
            ),
        }

    async def run(self) -> None:
        import asyncio

        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("s3_poll_failed")
            await asyncio.sleep(self._poll_seconds)

    async def _poll_once(self) -> None:
        async with self._session.client("s3", **self._options) as client:
            pending = await client.list_objects_v2(
                Bucket=self._bucket, Prefix="pending/", MaxKeys=10
            )
            for item in pending.get("Contents", []):
                await self._process_test(client, str(item["Key"]))
            if self._sub_store is not None:
                subs = await client.list_objects_v2(
                    Bucket=self._bucket, Prefix="subs/", MaxKeys=50
                )
                for item in subs.get("Contents", []):
                    await self._process_sub(client, str(item["Key"]))

    async def _process_test(self, client: Any, key: str) -> None:
        import asyncio

        response = await client.get_object(Bucket=self._bucket, Key=key)
        envelope = (await response["Body"].read()).decode()
        decrypted = self._cipher.decrypt(envelope)
        job_id = str(decrypted["job_id"])
        payload = decrypted["payload"]
        try:
            async with asyncio.timeout(self._operation_timeout):
                mode = str(payload.get("mode") or "full")
                if mode not in {"cheap", "full"}:
                    mode = "full"
                result = await run_test(
                    payload["config_uri"],
                    self._xray_binary,
                    self._test_url,
                    timeout=min(8, self._operation_timeout - 1),
                    mode=mode,
                )
                result["job_id"] = job_id
                result_envelope = self._cipher.encrypt(result)
                await client.put_object(
                    Bucket=self._bucket,
                    Key=f"results/{job_id}.enc",
                    Body=result_envelope.encode(),
                    ContentType="application/octet-stream",
                    ServerSideEncryption="AES256",
                )
        finally:
            await client.delete_object(Bucket=self._bucket, Key=key)

    async def _process_sub(self, client: Any, key: str) -> None:
        if self._sub_store is None:
            return
        response = await client.get_object(Bucket=self._bucket, Key=key)
        envelope = (await response["Body"].read()).decode()
        decrypted = self._cipher.decrypt(envelope)
        payload = decrypted.get("payload") or {}
        if payload.get("type") != "subscription_sync":
            return
        token = str(payload.get("token") or "")
        configs = payload.get("configs") or []
        if not token or not isinstance(configs, list):
            return
        self._sub_store.upsert(
            token,
            [str(item) for item in configs],
            expires_at=payload.get("expires_at"),
        )
        await client.delete_object(Bucket=self._bucket, Key=key)
        logger.info("sub_synced_from_s3", extra={"token": token[:8]})
