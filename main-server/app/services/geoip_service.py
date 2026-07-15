"""Resolves the ISO-3166 country code for a config's endpoint host.

Uses ip-api.com's free batch-less endpoint (no API key required, ~45
requests/minute) as the source of truth, with an in-process + Redis cache so
the same host is never looked up more than once every 30 days. Falls back to
the previous hostname-keyword heuristic when the network lookup fails, so a
transient outage never blocks the testing pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from urllib.parse import urlsplit

import httpx
from redis.asyncio import Redis

logger = logging.getLogger("subio.geoip")

_CACHE_PREFIX = "geoip:country:"
_CACHE_TTL_SECONDS = 30 * 24 * 3600
_LOOKUP_TIMEOUT = 3.0
_UNKNOWN = "XX"


def _hostname_heuristic(host: str) -> str:
    lowered = host.lower()
    if any(token in lowered for token in ("us", "usa", "america")):
        return "US"
    if any(token in lowered for token in ("tr", "tur", "istanbul")):
        return "TR"
    if any(token in lowered for token in ("de", "ger", "frankfurt")):
        return "DE"
    return _UNKNOWN


class GeoIpService:
    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis

    async def resolve_country(self, uri: str) -> str:
        host = (urlsplit(uri).hostname or "").strip()
        if not host:
            return _UNKNOWN

        cache_key = f"{_CACHE_PREFIX}{host}"
        if self._redis is not None:
            try:
                cached = await self._redis.get(cache_key)
            except Exception:
                cached = None
            if cached:
                return str(cached)

        country = await self._lookup(host)
        if self._redis is not None:
            try:
                await self._redis.set(cache_key, country, ex=_CACHE_TTL_SECONDS)
            except Exception:
                logger.warning("geoip_cache_write_failed", extra={"host": host})
        return country

    async def _lookup(self, host: str) -> str:
        try:
            ip = await self._resolve_ip(host)
            async with httpx.AsyncClient(timeout=_LOOKUP_TIMEOUT) as client:
                response = await client.get(
                    f"http://ip-api.com/json/{ip}",
                    params={"fields": "status,countryCode"},
                )
                response.raise_for_status()
                payload = response.json()
            if payload.get("status") == "success":
                code = str(payload.get("countryCode") or "").upper()
                if len(code) == 2:
                    return code
        except Exception:
            logger.warning("geoip_lookup_failed", extra={"host": host}, exc_info=True)
        return _hostname_heuristic(host)

    @staticmethod
    async def _resolve_ip(host: str) -> str:
        loop = asyncio.get_running_loop()
        info = await loop.getaddrinfo(host, None, family=socket.AF_INET)
        return info[0][4][0]
