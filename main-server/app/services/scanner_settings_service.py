"""Admin-togglable scanner and protocol settings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from redis.asyncio import Redis

from app.db import Database

ALL_PROTOCOLS = ("vless", "vmess", "trojan", "ss", "wireguard")
CACHE_KEY = "scanner:settings:v1"


@dataclass(frozen=True)
class ScannerSettings:
    npv_to_v2ray: bool
    decrypt_bot: bool
    protocols: frozenset[str]

    def allows_protocol(self, protocol: str) -> bool:
        return protocol.lower() in self.protocols

    def filter_uris(self, uris: set[str]) -> set[str]:
        return {uri for uri in uris if self.allows_protocol(urlsplit(uri).scheme.lower())}

    def to_dict(self) -> dict[str, Any]:
        return {
            "npv_to_v2ray": self.npv_to_v2ray,
            "decrypt_bot": self.decrypt_bot,
            "protocols": {name: name in self.protocols for name in ALL_PROTOCOLS},
        }


class ScannerSettingsService:
    def __init__(self, db: Database, cache: Redis) -> None:
        self._db = db
        self._cache = cache

    async def get(self) -> ScannerSettings:
        cached = await self._cache.get(CACHE_KEY)
        if cached:
            payload = json.loads(str(cached))
            enabled = {name for name, on in payload["protocols"].items() if on}
            return ScannerSettings(
                npv_to_v2ray=bool(payload["npv_to_v2ray"]),
                decrypt_bot=bool(payload["decrypt_bot"]),
                protocols=frozenset(enabled),
            )
        return await self._load_from_db()

    async def update(
        self,
        *,
        npv_to_v2ray: bool,
        decrypt_bot: bool,
        protocols: dict[str, bool],
    ) -> ScannerSettings:
        await self._upsert("scanner_npv_to_v2ray", "true" if npv_to_v2ray else "false")
        await self._upsert("scanner_decrypt_bot", "true" if decrypt_bot else "false")
        for name in ALL_PROTOCOLS:
            await self._upsert(
                f"scanner_protocol_{name}",
                "true" if protocols.get(name, False) else "false",
            )
        await self._cache.delete(CACHE_KEY)
        return await self.get()

    async def _load_from_db(self) -> ScannerSettings:
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text("SELECT key, value FROM system_messages WHERE key LIKE 'scanner_%'")
                )
            ).mappings().all()
        values = {str(row["key"]): str(row["value"]).lower() == "true" for row in rows}
        enabled = {
            name
            for name in ALL_PROTOCOLS
            if values.get(f"scanner_protocol_{name}", name in {"vless", "vmess", "trojan", "ss"})
        }
        settings = ScannerSettings(
            npv_to_v2ray=values.get("scanner_npv_to_v2ray", True),
            decrypt_bot=values.get("scanner_decrypt_bot", True),
            protocols=frozenset(enabled),
        )
        await self._cache.set(CACHE_KEY, json.dumps(settings.to_dict()), ex=60)
        return settings

    async def _upsert(self, key: str, value: str) -> None:
        await self._db.execute(
            """
            INSERT INTO system_messages(key, value) VALUES (:key, :value)
            ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=now()
            """,
            {"key": key, "value": value},
        )
