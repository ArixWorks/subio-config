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

    async def fetch_one(self, statement: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"config_code": "1", "country_code": "DE"}


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


class _FakeTransitionResult:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> "_FakeTransitionResult":
        return self

    def first(self) -> dict[str, Any] | None:
        return self._row


class _FakeTransitionConnection:
    """Simulates the vpn_configs row's consecutive_failures/is_enabled columns
    across successive UPDATE ... RETURNING calls, exactly like Postgres would."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _FakeTransitionResult:
        threshold = params["threshold"]
        self._state["consecutive_failures"] += 1
        if self._state["consecutive_failures"] >= threshold:
            self._state["is_enabled"] = False
        return _FakeTransitionResult(dict(self._state))


class _FakeTransitionDatabase:
    """Minimal stand-in exposing only what _apply_pool_transition touches for a
    single config row, tracking consecutive_failures/is_enabled like the real
    UPDATE ... RETURNING statement would."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {"consecutive_failures": 0, "is_enabled": True}

    @asynccontextmanager
    async def connection(self):
        yield _FakeTransitionConnection(self.state)

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def fetch_one(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"config_code": "1", "country_code": "DE"}


@pytest.mark.asyncio
async def test_retest_healthy_failure_does_not_demote_before_threshold(monkeypatch) -> None:
    """A healthy config that fails one 'cheap' retest_healthy probe must stay
    enabled — it should only be evicted from the public feed after
    `retest_demote_failure_threshold` *consecutive* failures. This prevents a
    genuinely healthy but higher-latency config from flapping in and out of the
    public subscription due to a single transient timeout.
    """
    from app import config as config_module

    settings = config_module.Settings.model_construct(retest_demote_failure_threshold=2)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)

    db = _FakeTransitionDatabase()
    service = ConfigTesterService(db, tester=None, cipher=_cipher(), scanner_settings=None)  # type: ignore[arg-type]

    # First consecutive failure: must remain enabled (below threshold).
    await service._apply_pool_transition(
        config_id="cfg-1",
        uri="ss://ok@203.0.113.9:9",
        protocol="ss",
        result={"latency_ms": 500},
        score=10.0,
        reachable=False,
        mode="cheap",
        purpose="retest_healthy",
        demote_on_first_fail=True,
    )
    assert db.state["is_enabled"] is True
    assert db.state["consecutive_failures"] == 1

    # Second consecutive failure: threshold reached, now demoted.
    await service._apply_pool_transition(
        config_id="cfg-1",
        uri="ss://ok@203.0.113.9:9",
        protocol="ss",
        result={"latency_ms": 500},
        score=10.0,
        reachable=False,
        mode="cheap",
        purpose="retest_healthy",
        demote_on_first_fail=True,
    )
    assert db.state["is_enabled"] is False
    assert db.state["consecutive_failures"] == 2


@pytest.mark.asyncio
async def test_discovery_failure_still_demotes_immediately(monkeypatch) -> None:
    """Non-retest_healthy failure paths (e.g. initial "discover" testing) must
    keep the original immediate-demote behavior — the consecutive-failure
    grace period only applies to the tight-cadence healthy-pool retest.
    """
    from app import config as config_module

    settings = config_module.Settings.model_construct(retest_demote_failure_threshold=2)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)

    executed: list[tuple[str, dict[str, Any] | None]] = []

    class _Database:
        async def execute(self, statement: str, values: dict[str, Any] | None = None) -> None:
            executed.append((statement, values))

        async def fetch_one(self, statement: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
            return {"config_code": "2", "country_code": "DE"}

    class _NullEvents:
        async def emit(self, **_kwargs: Any) -> None:
            return None

    service = ConfigTesterService(
        _Database(),
        tester=None,
        cipher=_cipher(),
        scanner_settings=None,
        pipeline_events=_NullEvents(),  # type: ignore[arg-type]
    )  # type: ignore[arg-type]

    await service._apply_pool_transition(
        config_id="cfg-2",
        uri="ss://dead@203.0.113.10:10",
        protocol="ss",
        result={"latency_ms": None},
        score=0.0,
        reachable=False,
        mode="full",
        purpose="discover",
        demote_on_first_fail=True,
    )

    assert len(executed) == 1
    assert "is_enabled=FALSE" in executed[0][0]
