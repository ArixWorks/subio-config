"""3x-ui panel integration via py3xui."""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from py3xui import AsyncApi, Client

from app.db import Database
from app.security import PayloadCipher

logger = logging.getLogger("subio.panel")

DAILY_BASE_BYTES = 1_073_741_824  # 1 GiB
SMART_SWITCH_FREE_THRESHOLD = 50 * 1024 * 1024  # 50 MiB
REFERRAL_BONUS_BYTES = 512 * 1024 * 1024  # 512 MiB per referral


@dataclass(frozen=True)
class PanelRecord:
    id: str
    name: str
    base_url: str
    username: str
    password: str


class PanelService:
    def __init__(self, db: Database, cipher: PayloadCipher) -> None:
        self._db = db
        self._cipher = cipher

    async def list_active(self) -> list[PanelRecord]:
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT id, name, base_url, username_enc, password_enc
                        FROM panels WHERE is_active ORDER BY name
                        """
                    )
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
                )
            )
        return result

    async def _api(self, panel: PanelRecord) -> AsyncApi:
        api = AsyncApi(panel.base_url, panel.username, panel.password)
        await api.login()
        return api

    async def create_private_client(
        self,
        *,
        subscription_id: str,
        user_id: int,
        location_code: str = "DE",
    ) -> dict[str, Any]:
        panels = await self.list_active()
        if not panels:
            raise RuntimeError("no active 3x-ui panels configured")
        panel = next((p for p in panels if location_code in p.name.upper()), panels[0])
        api = await self._api(panel)
        inbounds = await api.inbound.get_list()
        inbound = next((item for item in inbounds if getattr(item, "enable", True)), inbounds[0])
        client_uuid = str(uuid.uuid4())
        email = f"subio_{user_id}_{secrets.token_hex(4)}"
        sub_id = secrets.token_urlsafe(12)
        total_gb = DAILY_BASE_BYTES / (1024**3)
        client = Client(
            id=client_uuid,
            email=email,
            enable=True,
            flow="",
            limit_ip=2,
            total_gb=total_gb,
            expiry_time=0,
            sub_id=sub_id,
        )
        await api.client.add(inbound.id, [client])
        sub_link = f"{panel.base_url.rstrip('/')}/sub/{sub_id}"
        await self._db.execute(
            """
            INSERT INTO panel_clients(
              panel_id, subscription_id, inbound_id, client_uuid, client_email,
              sub_id, location_code
            ) VALUES (
              :panel_id, :subscription_id, :inbound_id, :client_uuid, :client_email,
              :sub_id, :location_code
            )
            """,
            {
                "panel_id": panel.id,
                "subscription_id": subscription_id,
                "inbound_id": inbound.id,
                "client_uuid": client_uuid,
                "client_email": email,
                "sub_id": sub_id,
                "location_code": location_code,
            },
        )
        return {
            "panel": panel.name,
            "location": location_code,
            "sub_id": sub_id,
            "subscription_url": sub_link,
            "client_uuid": client_uuid,
        }

    async def smart_switch(
        self, *, subscription_id: str, target_location: str
    ) -> dict[str, Any]:
        row = await self._db.fetch_one(
            """
            SELECT s.volume_used_bytes, s.user_id, pc.id AS client_id, pc.panel_id, pc.client_email,
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
        panel = next((p for p in panels if target_location in p.name.upper()), None)
        if panel is None:
            raise RuntimeError(f"no panel for location {target_location}")
        api = await self._api(panel)
        await api.client.delete(int(row["inbound_id"]), str(row["client_email"]))
        await self._db.execute(
            "UPDATE panel_clients SET is_active=FALSE WHERE id=:id",
            {"id": row["client_id"]},
        )
        created = await self.create_private_client(
            subscription_id=subscription_id,
            user_id=int(row["user_id"]),
            location_code=target_location,
        )
        return {"switched": True, **created}

    async def apply_referral_bonus(self, referrer_id: int) -> None:
        await self._db.execute(
            """
            UPDATE users
            SET referral_credit_bytes = referral_credit_bytes + :bonus
            WHERE telegram_id=:referrer_id
            """,
            {"referrer_id": referrer_id, "bonus": REFERRAL_BONUS_BYTES},
        )
        row = await self._db.fetch_one(
            """
            SELECT id FROM subscriptions
            WHERE user_id=:referrer_id AND is_active AND expires_at > now()
            ORDER BY expires_at DESC LIMIT 1
            """,
            {"referrer_id": referrer_id},
        )
        if row:
            await self._db.execute(
                """
                UPDATE subscriptions
                SET volume_limit_bytes = volume_limit_bytes + :bonus
                WHERE id=:id
                """,
                {"id": row["id"], "bonus": REFERRAL_BONUS_BYTES},
            )
