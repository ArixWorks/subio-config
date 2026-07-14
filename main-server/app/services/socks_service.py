"""SOCKS proxy CRUD, URI parsing, and sync to Iran tester."""

from __future__ import annotations

import base64
import logging
import re
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from app.db import Database
from app.security import PayloadCipher, signed_headers

logger = logging.getLogger("subio.socks")

URI_LINE = re.compile(
    r"^(?:socks4|socks5|socks)://[^\s]+$",
    re.IGNORECASE,
)


def parse_socks_uri(raw: str) -> dict[str, Any]:
    """Parse socks://user:pass@host:port#name (also base64 userinfo)."""
    text = raw.strip()
    if "#" in text:
        text, remark = text.split("#", 1)
        name = unquote(remark)
    else:
        name = ""
    if "://" not in text:
        raise ValueError("invalid socks uri")
    scheme, rest = text.split("://", 1)
    scheme = scheme.lower()
    if scheme == "socks":
        scheme = "socks5"
    if scheme not in {"socks4", "socks5"}:
        raise ValueError("unsupported socks protocol")
    parsed = urlparse(f"{scheme}://{rest}")
    if not parsed.hostname or not parsed.port:
        raise ValueError("host/port required")
    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    if username and password is None and ":" not in username:
        try:
            decoded = base64.b64decode(username + "==").decode("utf-8", errors="strict")
            if ":" in decoded:
                username, password = decoded.split(":", 1)
        except Exception:
            pass
    return {
        "name": name or f"{parsed.hostname}:{parsed.port}",
        "host": parsed.hostname,
        "port": int(parsed.port),
        "username": username,
        "password": password,
        "protocol": scheme,
    }


class SocksService:
    def __init__(
        self,
        db: Database,
        cipher: PayloadCipher,
        tester_base_url: str,
        *,
        hmac_key: str | None = None,
        payload_cipher: PayloadCipher | None = None,
    ) -> None:
        self._db = db
        self._cipher = cipher
        self._tester_base_url = tester_base_url.rstrip("/")
        self._hmac_key = hmac_key
        self._payload_cipher = payload_cipher or cipher

    async def list_proxies(self) -> list[dict[str, Any]]:
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT id, name, host, port, username, protocol, priority,
                               is_active, last_checked_at, last_latency_ms, success_rate, fail_count
                        FROM socks_proxies ORDER BY priority ASC, id ASC
                        """
                    )
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def upsert(
        self,
        *,
        name: str,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        protocol: str = "socks5",
        priority: int = 0,
        is_active: bool = True,
        proxy_id: int | None = None,
    ) -> None:
        password_enc = None
        if password:
            password_enc = self._cipher.encrypt({"password": password}, aad=b"subio:socks:v1")
        if proxy_id is not None:
            await self._db.execute(
                """
                UPDATE socks_proxies SET
                  name=:name, host=:host, port=:port, username=:username,
                  password_enc=COALESCE(:password_enc, password_enc),
                  protocol=:protocol, priority=:priority, is_active=:is_active
                WHERE id=:id
                """,
                {
                    "id": proxy_id,
                    "name": name,
                    "host": host,
                    "port": port,
                    "username": username,
                    "password_enc": password_enc,
                    "protocol": protocol,
                    "priority": priority,
                    "is_active": is_active,
                },
            )
            return
        await self._db.execute(
            """
            INSERT INTO socks_proxies(name, host, port, username, password_enc, protocol, priority, is_active)
            VALUES (:name, :host, :port, :username, :password_enc, :protocol, :priority, :is_active)
            ON CONFLICT (host, port, username) DO UPDATE SET
              name=excluded.name,
              password_enc=COALESCE(excluded.password_enc, socks_proxies.password_enc),
              protocol=excluded.protocol, priority=excluded.priority, is_active=excluded.is_active
            """,
            {
                "name": name,
                "host": host,
                "port": port,
                "username": username,
                "password_enc": password_enc,
                "protocol": protocol,
                "priority": priority,
                "is_active": is_active,
            },
        )

    async def upsert_from_uri(self, uri: str, *, priority: int = 0) -> dict[str, Any]:
        parsed = parse_socks_uri(uri)
        await self.upsert(**parsed, priority=priority)
        return parsed

    async def upsert_many_uris(self, text: str) -> dict[str, int]:
        lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
        added = 0
        skipped = 0
        for index, line in enumerate(lines):
            try:
                await self.upsert_from_uri(line, priority=index)
                added += 1
            except ValueError:
                skipped += 1
        return {"added": added, "skipped": skipped}

    async def delete(self, proxy_id: int) -> None:
        await self._db.execute("DELETE FROM socks_proxies WHERE id=:id", {"id": proxy_id})

    async def set_active(self, proxy_id: int, is_active: bool) -> None:
        await self._db.execute(
            "UPDATE socks_proxies SET is_active=:active WHERE id=:id",
            {"id": proxy_id, "active": is_active},
        )

    async def trigger_health_check(self) -> list[dict[str, Any]]:
        await self.sync_to_tester()
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{self._tester_base_url}/v1/socks/check")
            response.raise_for_status()
            results = response.json()
        for item in results:
            host_port = str(item.get("uri", ""))
            await self._db.execute(
                """
                UPDATE socks_proxies
                SET last_checked_at=now(),
                    last_latency_ms=:latency,
                    success_rate=:rate,
                    fail_count=CASE WHEN :rate < 50 THEN fail_count+1 ELSE fail_count END
                WHERE host || ':' || port::text = split_part(:uri, '@', 2)
                   OR host || ':' || port::text = :uri
                """,
                {
                    "latency": item.get("latency_ms"),
                    "rate": item.get("success_rate", 100),
                    "uri": host_port,
                },
            )
        return results

    async def proxy_uris_for_tester(self) -> list[str]:
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT host, port, username, password_enc, protocol, priority
                        FROM socks_proxies WHERE is_active ORDER BY priority, id
                        """
                    )
                )
            ).mappings().all()
        uris: list[str] = []
        for row in rows:
            auth = ""
            if row.get("username"):
                password = ""
                if row.get("password_enc"):
                    password = self._cipher.decrypt(
                        str(row["password_enc"]), aad=b"subio:socks:v1"
                    )["password"]
                auth = f"{row['username']}:{password}@"
            uris.append(f"{row['protocol']}://{auth}{row['host']}:{row['port']}")
        return uris

    async def sync_to_tester(self) -> dict[str, Any]:
        if not self._hmac_key:
            raise RuntimeError("hmac key required for socks sync")
        uris = await self.proxy_uris_for_tester()
        envelope = self._payload_cipher.encrypt(
            {"job_id": "socks-sync", "payload": {"type": "socks_reload", "proxies": uris}}
        )
        headers = signed_headers(envelope, self._hmac_key)
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{self._tester_base_url}/v1/socks/reload",
                json={"envelope": envelope},
                headers=headers,
            )
            response.raise_for_status()
            return response.json()
