"""Resilient communication manager with DB persistence and health probes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from prometheus_client import Gauge

from app.communication import BreakerState, CircuitBreaker, ResilientTesterClient
from app.db import Database

logger = logging.getLogger("subio.comm")
COMM_MODE = Gauge("subio_comm_mode", "1 when direct, 0 when S3 fallback")


@dataclass
class CommState:
    mode: str
    forced_mode: str | None
    consecutive_failures: int
    recovery_successes: int
    probe_interval_sec: int
    fail_threshold: int


class CommunicationManager:
    """Coordinates circuit breaker state with PostgreSQL and switch audit logs."""

    def __init__(self, db: Database, tester: ResilientTesterClient) -> None:
        self._db = db
        self._tester = tester

    async def load(self) -> CommState:
        row = await self._db.fetch_one(
            """
            SELECT mode, forced_mode, consecutive_failures, recovery_successes,
                   probe_interval_sec, fail_threshold
            FROM system_comm_state WHERE singleton IS TRUE
            """,
            {},
        )
        if row is None:
            raise RuntimeError("system_comm_state row missing")
        state = CommState(
            mode=str(row["mode"]),
            forced_mode=row.get("forced_mode"),
            consecutive_failures=int(row["consecutive_failures"]),
            recovery_successes=int(row["recovery_successes"]),
            probe_interval_sec=int(row["probe_interval_sec"]),
            fail_threshold=int(row["fail_threshold"]),
        )
        breaker = self._tester._breaker
        breaker.failure_threshold = state.fail_threshold
        if state.mode == "arvan_s3":
            breaker.state = BreakerState.OPEN
            breaker.opened_at = time.monotonic()
        COMM_MODE.set(1 if state.mode == "direct" else 0)
        return state

    async def probe_and_reconcile(self) -> dict[str, Any]:
        state = await self.load()
        if state.forced_mode:
            await self._switch_to(state.forced_mode, reason=f"forced mode: {state.forced_mode}")
            return {"mode": state.forced_mode, "forced": True}

        ok = await self._tester.probe()
        if ok:
            await self._record_success()
            if state.mode == "arvan_s3":
                row = await self._db.fetch_one(
                    "SELECT recovery_successes FROM system_comm_state WHERE singleton IS TRUE",
                    {},
                )
                if row and int(row["recovery_successes"]) >= self._tester._breaker.recovery_threshold:
                    await self._switch_to("direct", reason="direct probe recovered")
            return {"mode": "direct", "probe": "ok"}

        await self._record_failure()
        row = await self._db.fetch_one(
            "SELECT consecutive_failures, mode FROM system_comm_state WHERE singleton IS TRUE",
            {},
        )
        failures = int(row["consecutive_failures"]) if row else 0
        current_mode = str(row["mode"]) if row else "direct"
        if failures >= state.fail_threshold and current_mode == "direct":
            await self._switch_to("arvan_s3", reason=f"{failures} consecutive probe failures")
            return {"mode": "arvan_s3", "probe": "failed", "failures": failures}
        return {"mode": current_mode, "probe": "failed", "failures": failures}

    async def force_mode(self, mode: str, *, reason: str = "admin force") -> None:
        if mode not in {"direct", "arvan_s3"}:
            raise ValueError("mode must be direct or arvan_s3")
        await self._db.execute(
            """
            UPDATE system_comm_state
            SET forced_mode=:mode, mode=:mode, last_switch_at=now()
            WHERE singleton IS TRUE
            """,
            {"mode": mode},
        )
        await self._switch_to(mode, reason=reason, forced=True)

    async def clear_force(self) -> None:
        await self._db.execute(
            "UPDATE system_comm_state SET forced_mode=NULL WHERE singleton IS TRUE",
            {},
        )

    async def recent_switches(self, limit: int = 50) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            result = await conn.execute(
                text(
                    """
                    SELECT from_mode, to_mode, reason, created_at
                    FROM comm_switch_logs ORDER BY created_at DESC LIMIT :limit
                    """
                ),
                {"limit": limit},
            )
            rows = [dict(row) for row in result.mappings().all()]
        return rows

    async def _record_success(self) -> None:
        await self._db.execute(
            """
            UPDATE system_comm_state
            SET consecutive_failures=0,
                recovery_successes=recovery_successes+1,
                last_direct_success=now()
            WHERE singleton IS TRUE
            """,
            {},
        )

    async def _record_failure(self) -> None:
        await self._db.execute(
            """
            UPDATE system_comm_state
            SET consecutive_failures=consecutive_failures+1, recovery_successes=0
            WHERE singleton IS TRUE
            """,
            {},
        )

    async def _switch_to(self, mode: str, *, reason: str, forced: bool = False) -> None:
        row = await self._db.fetch_one(
            "SELECT mode FROM system_comm_state WHERE singleton IS TRUE",
            {},
        )
        from_mode = str(row["mode"]) if row else "direct"
        if from_mode == mode and not forced:
            return
        await self._db.execute(
            """
            UPDATE system_comm_state
            SET mode=:mode, last_switch_at=now(),
                consecutive_failures=CASE WHEN :mode='direct' THEN 0 ELSE consecutive_failures END
            WHERE singleton IS TRUE
            """,
            {"mode": mode},
        )
        await self._db.execute(
            """
            INSERT INTO comm_switch_logs(from_mode, to_mode, reason)
            VALUES (:from_mode, :to_mode, :reason)
            """,
            {"from_mode": from_mode, "to_mode": mode, "reason": reason},
        )
        breaker = self._tester._breaker
        if mode == "arvan_s3":
            breaker.state = BreakerState.OPEN
            breaker.opened_at = time.monotonic()
        else:
            breaker.state = BreakerState.CLOSED
            breaker.failures = 0
        COMM_MODE.set(1 if mode == "direct" else 0)
        logger.warning("comm_mode_switch", extra={"from": from_mode, "to": mode, "reason": reason})
