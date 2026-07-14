"""3x-ui panel integration via py3xui."""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from py3xui import AsyncApi, Client
from sqlalchemy import text

from app.db import Database
from app.security import PayloadCipher

logger = logging.getLogger("subio.panel")

DAILY_BASE_BYTES = 1_073_741_824  # 1 GiB
SMART_SWITCH_FREE_THRESHOLD = 50 * 1024 * 1024  # 50 MiB
REFERRAL_BONUS_BYTES = 512 * 1024 * 1024  # 512 MiB per referral
TEHRAN = ZoneInfo("Asia/Tehran")


@dataclass(frozen=True)
class PanelRecord:
    id: str
    name: str
    base_url: str
    username: str
    password: str
    country_code: str
    country_name_fa: str
    flag_emoji_key: str
    sort_order: int


def next_tehran_midnight(now: datetime | None = None) -> datetime:
    current = (now or datetime.now(tz=UTC)).astimezone(TEHRAN)
    tomorrow = current.date() + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=TEHRAN).astimezone(UTC)


def tehran_service_day(now: datetime | None = None) -> date:
    return (now or datetime.now(tz=UTC)).astimezone(TEHRAN).date()


def country_flag(country_code: str) -> str:
    code = country_code.upper()
    if len(code) != 2 or not code.isalpha():
        return "🌍"
    return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in code)


class PanelService:
    def __init__(self, db: Database, cipher: PayloadCipher) -> None:
        self._db = db
        self._cipher = cipher

    async def list_active(self) -> list[PanelRecord]:
        return await self._list_records(active_only=True)

    async def list_all(self) -> list[PanelRecord]:
        return await self._list_records(active_only=False)

    async def _list_records(self, *, active_only: bool) -> list[PanelRecord]:
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT id, name, base_url, username_enc, password_enc,
                               COALESCE(country_code, 'UN') AS country_code,
                               COALESCE(country_name_fa, name) AS country_name_fa,
                               COALESCE(flag_emoji_key, 'location') AS flag_emoji_key,
                               sort_order
                        FROM panels
                        WHERE (:active_only=FALSE OR is_active)
                        ORDER BY sort_order, country_name_fa, name
                        """
                    ),
                    {"active_only": active_only},
                )
            ).mappings().all()
        result: list[PanelRecord] = []
        for row in rows:
            creds = self._cipher.decrypt(str(row["username_enc"]), aad=b"subio:panel:v1")
            pass_creds = self._cipher.decrypt(str(row["password_enc"]), aad=b"subio:panel:v1")
            result.append(
                PanelRecord(
                    id=str(row["id"]),
                    name=str(row["name"]),
                    base_url=str(row["base_url"]),
                    username=str(creds["username"]),
                    password=str(pass_creds["password"]),
                    country_code=str(row["country_code"]).upper(),
                    country_name_fa=str(row["country_name_fa"]),
                    flag_emoji_key=str(row["flag_emoji_key"]),
                    sort_order=int(row["sort_order"]),
                )
            )
        return result

    async def list_locations(self) -> list[dict[str, str]]:
        rows = await self._db.fetch_all(
            """
            SELECT country_code, country_name_fa, flag_emoji_key
            FROM (
              SELECT DISTINCT ON (country_code)
                     country_code, country_name_fa, flag_emoji_key, sort_order, name
              FROM panels
              WHERE is_active AND country_code IS NOT NULL AND country_name_fa IS NOT NULL
              ORDER BY country_code, sort_order, name
            ) locations
            ORDER BY sort_order, country_name_fa
            """
        )
        return [
            {
                "code": str(row["country_code"]).upper(),
                "name_fa": str(row["country_name_fa"]),
                "flag_emoji_key": str(row.get("flag_emoji_key") or "location"),
                "fallback_flag": country_flag(str(row["country_code"])),
            }
            for row in rows
        ]

    async def _api(self, panel: PanelRecord) -> AsyncApi:
        api = AsyncApi(panel.base_url, panel.username, panel.password)
        await api.login()
        return api

    async def create_private_client(
        self,
        *,
        subscription_id: str,
        user_id: int,
        location_code: str,
        expires_at: datetime,
        volume_bytes: int = DAILY_BASE_BYTES,
        usage_offset_bytes: int = 0,
    ) -> dict[str, Any]:
        panels = await self.list_active()
        if not panels:
            raise RuntimeError("no active 3x-ui panels configured")
        normalized_location = location_code.upper()
        panel = next((p for p in panels if p.country_code == normalized_location), None)
        if panel is None:
            raise RuntimeError(f"no active panel for location {normalized_location}")
        client_uuid = str(uuid.uuid4())
        email = f"subio_{user_id}_{secrets.token_hex(4)}"
        sub_id = secrets.token_urlsafe(12)
        remote_created = False
        api: AsyncApi | None = None
        inbound: Any = None
        provision_error: Exception | None = None
        async with self._db.connection() as conn:
            await conn.execute(
                text(
                    "SELECT pg_advisory_xact_lock_shared("
                    "hashtextextended(CAST(:panel_id AS text), 0))"
                ),
                {"panel_id": panel.id},
            )
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT id, name, base_url, username_enc, password_enc,
                               country_code, country_name_fa, flag_emoji_key, sort_order
                        FROM panels
                        WHERE id=:panel_id AND is_active
                        """
                    ),
                    {"panel_id": panel.id},
                )
            ).mappings().first()
            if row is None:
                raise RuntimeError("selected panel is no longer active")
            username = self._cipher.decrypt(
                str(row["username_enc"]), aad=b"subio:panel:v1"
            )
            password = self._cipher.decrypt(
                str(row["password_enc"]), aad=b"subio:panel:v1"
            )
            panel = PanelRecord(
                id=str(row["id"]),
                name=str(row["name"]),
                base_url=str(row["base_url"]),
                username=str(username["username"]),
                password=str(password["password"]),
                country_code=str(row["country_code"]).upper(),
                country_name_fa=str(row["country_name_fa"]),
                flag_emoji_key=str(row["flag_emoji_key"]),
                sort_order=int(row["sort_order"]),
            )
            api = await self._api(panel)
            inbounds = await api.inbound.get_list()
            if not inbounds:
                raise RuntimeError(f"panel {panel.name} has no inbounds")
            inbound = next(
                (item for item in inbounds if getattr(item, "enable", True)),
                inbounds[0],
            )
            client = Client(
                id=client_uuid,
                email=email,
                enable=True,
                flow="",
                limit_ip=2,
                total_gb=volume_bytes,
                expiry_time=int(expires_at.timestamp() * 1000),
                sub_id=sub_id,
            )
            try:
                remote_created = True
                await api.client.add(inbound.id, [client])
                persisted_client = await api.client.get_by_email(email)
                if persisted_client is None:
                    raise RuntimeError("panel did not persist the new client")
                persisted_client.expiry_time = int(expires_at.timestamp() * 1000)
                persisted_client.total_gb = volume_bytes
                persisted_client.limit_ip = 2
                persisted_client.sub_id = sub_id
                await api.client.update(client_uuid, persisted_client)
                async with conn.begin_nested():
                    await conn.execute(
                        text(
                            """
                            INSERT INTO panel_clients(
                              panel_id, subscription_id, inbound_id, client_uuid, client_email,
                              sub_id, location_code, usage_offset_bytes
                            ) VALUES (
                              :panel_id, :subscription_id, :inbound_id, :client_uuid, :client_email,
                              :sub_id, :location_code, :usage_offset_bytes
                            )
                            """
                        ),
                        {
                            "panel_id": panel.id,
                            "subscription_id": subscription_id,
                            "inbound_id": inbound.id,
                            "client_uuid": client_uuid,
                            "client_email": email,
                            "sub_id": sub_id,
                            "location_code": normalized_location,
                            "usage_offset_bytes": usage_offset_bytes,
                        },
                    )
            except Exception as exc:
                provision_error = exc
                if remote_created and api is not None and inbound is not None:
                    try:
                        await api.client.delete(inbound.id, client_uuid)
                    except Exception:
                        logger.exception(
                            "panel_client_provision_rollback_failed",
                            extra={"panel": panel.name, "client_email": email},
                        )
                        await conn.execute(
                            text(
                                """
                                INSERT INTO panel_cleanup_jobs(
                                  panel_id, inbound_id, client_uuid, client_email,
                                  attempts, last_error
                                ) VALUES (
                                  :panel_id, :inbound_id, :client_uuid, :client_email,
                                  1, :last_error
                                )
                                ON CONFLICT (panel_id, client_uuid) DO UPDATE
                                SET attempts=panel_cleanup_jobs.attempts+1,
                                    last_error=excluded.last_error,
                                    updated_at=now()
                                """
                            ),
                            {
                                "panel_id": panel.id,
                                "inbound_id": inbound.id,
                                "client_uuid": client_uuid,
                                "client_email": email,
                                "last_error": str(exc)[:1000],
                            },
                        )
        if provision_error is not None:
            raise provision_error
        sub_link = f"{panel.base_url.rstrip('/')}/sub/{sub_id}"
        return {
            "panel": panel.name,
            "location": normalized_location,
            "country_name_fa": panel.country_name_fa,
            "sub_id": sub_id,
            "subscription_url": sub_link,
            "client_uuid": client_uuid,
            "expires_at": expires_at,
        }

    async def smart_switch(
        self, *, subscription_id: str, target_location: str
    ) -> dict[str, Any]:
        row = await self._db.fetch_one(
            """
            SELECT s.volume_used_bytes, s.volume_limit_bytes, s.user_id,
                   pc.id AS client_id, pc.panel_id, pc.client_email, pc.client_uuid,
                   pc.inbound_id, pc.location_code
            FROM subscriptions s
            JOIN panel_clients pc ON pc.subscription_id = s.id AND pc.is_active
            WHERE s.id=:subscription_id
            LIMIT 1
            """,
            {"subscription_id": subscription_id},
        )
        if not row:
            raise RuntimeError("no active panel client for subscription")
        used = int(row["volume_used_bytes"])
        if used >= SMART_SWITCH_FREE_THRESHOLD:
            raise RuntimeError("smart switch is free only under 50MB usage")
        current_location = str(row["location_code"])
        if current_location == target_location:
            return {"switched": False, "location": current_location}
        panels = await self.list_active()
        all_panels = {panel.id: panel for panel in await self.list_all()}
        target_panel = next(
            (panel for panel in panels if panel.country_code == target_location.upper()), None
        )
        current_panel = all_panels.get(str(row["panel_id"]))
        if target_panel is None:
            raise RuntimeError(f"no panel for location {target_location}")
        if current_panel is None:
            raise RuntimeError("current panel is no longer active")
        current_api = await self._api(current_panel)
        await current_api.client.delete(int(row["inbound_id"]), str(row["client_uuid"]))
        await self._db.execute(
            "UPDATE panel_clients SET is_active=FALSE WHERE id=:id",
            {"id": row["client_id"]},
        )
        created = await self.create_private_client(
            subscription_id=subscription_id,
            user_id=int(row["user_id"]),
            location_code=target_location,
            expires_at=next_tehran_midnight(),
            volume_bytes=max(1, int(row["volume_limit_bytes"]) - used),
            usage_offset_bytes=used,
        )
        return {"switched": True, **created}

    async def sync_usage(self) -> dict[str, int]:
        rows = await self._db.fetch_all(
            """
            SELECT pc.panel_id, pc.client_email, pc.subscription_id, pc.usage_offset_bytes
            FROM panel_clients pc
            JOIN subscriptions s ON s.id=pc.subscription_id
            WHERE pc.is_active AND s.is_active AND s.expires_at>now()
            """
        )
        panels = {panel.id: panel for panel in await self.list_all()}
        apis: dict[str, AsyncApi] = {}
        updated = 0
        failed = 0
        for row in rows:
            panel_id = str(row["panel_id"])
            panel = panels.get(panel_id)
            if panel is None:
                failed += 1
                continue
            try:
                api = apis.get(panel_id)
                if api is None:
                    api = await self._api(panel)
                    apis[panel_id] = api
                client = await api.client.get_by_email(str(row["client_email"]))
                if client is None:
                    raise RuntimeError("panel client not found")
                used = (
                    int(row["usage_offset_bytes"])
                    + max(0, int(getattr(client, "up", 0) or 0))
                    + max(0, int(getattr(client, "down", 0) or 0))
                )
                await self._db.execute(
                    """
                    UPDATE subscriptions
                    SET volume_used_bytes=LEAST(:used, volume_limit_bytes)
                    WHERE id=:subscription_id
                    """,
                    {"used": used, "subscription_id": row["subscription_id"]},
                )
                updated += 1
            except Exception:
                failed += 1
                logger.exception(
                    "panel_usage_sync_failed",
                    extra={"panel": panel.name, "client_email": row["client_email"]},
                )
        return {"updated": updated, "failed": failed}

    async def revoke_inactive_clients(self, user_id: int | None = None) -> dict[str, int]:
        rows = await self._db.fetch_all(
            """
            SELECT pc.id, pc.panel_id, pc.inbound_id, pc.client_uuid, pc.client_email
            FROM panel_clients pc
            JOIN subscriptions s ON s.id=pc.subscription_id
            WHERE pc.is_active
              AND (NOT s.is_active OR s.expires_at<=now())
              AND (CAST(:user_id AS BIGINT) IS NULL OR s.user_id=CAST(:user_id AS BIGINT))
            """,
            {"user_id": user_id},
        )
        panels = {panel.id: panel for panel in await self.list_all()}
        apis: dict[str, AsyncApi] = {}
        revoked = 0
        failed = 0
        for row in rows:
            panel_id = str(row["panel_id"])
            panel = panels.get(panel_id)
            if panel is None:
                failed += 1
                continue
            try:
                api = apis.get(panel_id)
                if api is None:
                    api = await self._api(panel)
                    apis[panel_id] = api
                await api.client.delete(int(row["inbound_id"]), str(row["client_uuid"]))
                await self._db.execute(
                    "UPDATE panel_clients SET is_active=FALSE WHERE id=:id",
                    {"id": row["id"]},
                )
                revoked += 1
            except Exception:
                failed += 1
                logger.exception(
                    "panel_client_revoke_failed",
                    extra={"panel": panel.name, "client_email": row["client_email"]},
                )
        return {"revoked": revoked, "failed": failed}

    async def process_cleanup_jobs(self, limit: int = 100) -> dict[str, int]:
        rows = await self._db.fetch_all(
            """
            SELECT id, panel_id, inbound_id, client_uuid, client_email
            FROM panel_cleanup_jobs
            ORDER BY created_at
            LIMIT :limit
            """,
            {"limit": limit},
        )
        panels = {panel.id: panel for panel in await self.list_all()}
        apis: dict[str, AsyncApi] = {}
        cleaned = 0
        failed = 0
        for row in rows:
            panel_id = str(row["panel_id"])
            panel = panels.get(panel_id)
            if panel is None:
                failed += 1
                continue
            try:
                api = apis.get(panel_id)
                if api is None:
                    api = await self._api(panel)
                    apis[panel_id] = api
                client = await api.client.get_by_email(str(row["client_email"]))
                if client is not None:
                    await api.client.delete(
                        int(row["inbound_id"]), str(row["client_uuid"])
                    )
                await self._db.execute(
                    "DELETE FROM panel_cleanup_jobs WHERE id=:id",
                    {"id": row["id"]},
                )
                cleaned += 1
            except Exception as exc:
                failed += 1
                await self._db.execute(
                    """
                    UPDATE panel_cleanup_jobs
                    SET attempts=attempts+1, last_error=:error, updated_at=now()
                    WHERE id=:id
                    """,
                    {"id": row["id"], "error": str(exc)[:1000]},
                )
                logger.exception(
                    "panel_cleanup_job_failed",
                    extra={"panel": panel.name, "client_email": row["client_email"]},
                )
        return {"cleaned": cleaned, "failed": failed}
