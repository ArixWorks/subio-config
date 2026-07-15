"""Regression tests for the public-config discovery batch resilience."""

from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.communication import CommunicationUnavailable
from app.security import PayloadCipher
from app.services.config_tester import ConfigTesterService


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_FakeResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConnection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult(self._rows)


class _FakeEngine:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    @asynccontextmanager
    async def connect(self):
        yield _FakeConnection(self._rows)


class _FakeDatabase:
    """Minimal stand-in exposing only what queue_untested_public_configs touches."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.engine = _FakeEngine(rows)
        self.executed: list[tuple[str, dict[str, Any] | None]] = []

    async def execute(self, statement: str, values: dict[str, Any] | None = None) -> None:
        self.executed.append((statement, values))


def _cipher() -> PayloadCipher:
    return PayloadCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())


@pytest.mark.asyncio
async def test_queue_untested_public_configs_skips_poison_pill_and_continues() -> None:
    """A single config that raises CommunicationUnavailable (e.g. the Iran tester
    times out reaching it) must not abort the whole discovery batch — every other
    config in the batch must still be attempted. This guards against the
    "poison-pill" regression where one dead config sitting at the head of the
    untested queue (ORDER BY created_at ASC) deterministically re-triggered the
    same timeout on every cron run and starved the rest of the queue forever.
    """
    cipher = _cipher()
    rows = [
        {
            "id": "poison",
            "protocol": "ss",
            "uri_enc": cipher.encrypt({"uri": "ss://dead@203.0.113.1:1"}, aad=b"subio:config:v1"),
        },
        {
            "id": "healthy-1",
            "protocol": "ss",
            "uri_enc": cipher.encrypt({"uri": "ss://ok@203.0.113.2:2"}, aad=b"subio:config:v1"),
        },
        {
            "id": "healthy-2",
            "protocol": "ss",
            "uri_enc": cipher.encrypt({"uri": "ss://ok@203.0.113.3:3"}, aad=b"subio:config:v1"),
        },
    ]
    db = _FakeDatabase(rows)

    class _Service(ConfigTesterService):
        def __init__(self) -> None:
            super().__init__(db, tester=None, cipher=cipher, scanner_settings=None)  # type: ignore[arg-type]
            self.attempted: list[str] = []

        async def test_and_store(self, *, config_id, uri, protocol, mode="full", purpose="manual"):  # type: ignore[override]
            self.attempted.append(config_id)
            if config_id == "poison":
                raise CommunicationUnavailable("tester operation exceeded 18 seconds")
            return {"job_id": "x", "score": 100.0, "mode": mode, "reachable": True}

    service = _Service()
    queued = await service.queue_untested_public_configs(limit=20)

    # All three rows must have been attempted despite the first one failing.
    assert service.attempted == ["poison", "healthy-1", "healthy-2"]
    # Only the two healthy configs count toward the returned "queued" total.
    assert queued == 2
