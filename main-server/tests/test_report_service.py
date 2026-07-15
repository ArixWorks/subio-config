"""Unit tests for per-operator (5) and global (10) report thresholds."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.services.report_service import (
    GLOBAL_THRESHOLD,
    PER_OPERATOR_THRESHOLD,
    ReportService,
)


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row

    def mappings(self) -> "_FakeResult":
        return self


class _FakeConnection:
    def __init__(self, db: "_FakeDatabase") -> None:
        self._db = db

    async def execute(self, statement: Any, params: dict[str, Any]) -> _FakeResult:
        sql = str(statement)
        if "INSERT INTO config_reports" in sql:
            key = (params["config_id"], params["reporter"], params["operator"])
            if key in self._db.reports:
                return _FakeResult(None)
            self._db.reports.add(key)
            return _FakeResult({"id": "new"})
        if "INSERT INTO config_operator_exclusions" in sql:
            key = (params["config_id"], params["operator"])
            prev = self._db.exclusions.get(key, 0)
            if params["count"] <= prev:
                return _FakeResult(None)
            self._db.exclusions[key] = params["count"]
            return _FakeResult({"config_id": params["config_id"]})
        raise AssertionError(f"unexpected statement: {sql}")


class _FakeNullEvents:
    async def emit(self, **_kwargs: Any) -> None:
        return None


class _FakeDatabase:
    """Simulates the tiny slice of Postgres state ReportService touches:
    config_reports (dedup by config/reporter/operator) and
    config_operator_exclusions (per-operator report_count), plus
    vpn_configs.is_globally_blocked.
    """

    def __init__(self) -> None:
        self.reports: set[tuple[str, int, str]] = set()
        self.exclusions: dict[tuple[str, str], int] = {}
        self.globally_blocked: set[str] = set()
        self.config_codes: dict[str, str] = {"cfg-1": "454"}

    @asynccontextmanager
    async def connection(self):
        yield _FakeConnection(self)

    async def fetch_one(self, statement: str, values: dict[str, Any] | None = None) -> dict[str, Any] | None:
        values = values or {}
        if "SELECT config_code FROM vpn_configs WHERE id" in statement:
            return {"config_code": self.config_codes.get(values["id"], "")}
        if "COUNT(*) AS n FROM config_reports WHERE config_id=:id AND operator_code" in statement:
            count = sum(
                1 for (cid, _reporter, op) in self.reports if cid == values["id"] and op == values["operator"]
            )
            return {"n": count}
        if "COUNT(*) AS n FROM config_reports WHERE config_id=:id" in statement:
            count = sum(1 for (cid, _reporter, _op) in self.reports if cid == values["id"])
            return {"n": count}
        if "is_globally_blocked FROM vpn_configs" in statement:
            return {"is_globally_blocked": values["id"] in self.globally_blocked}
        raise AssertionError(f"unexpected fetch_one: {statement}")

    async def execute(self, statement: str, values: dict[str, Any] | None = None) -> None:
        values = values or {}
        if "is_globally_blocked=TRUE" in statement:
            self.globally_blocked.add(values["id"])
            return
        raise AssertionError(f"unexpected execute: {statement}")


@pytest.mark.asyncio
async def test_single_report_never_excludes_or_blocks() -> None:
    db = _FakeDatabase()
    service = ReportService(db, pipeline_events=_FakeNullEvents())  # type: ignore[arg-type]

    outcome = await service.submit_report(
        config_id="cfg-1", reporter_user_id=1, operator_code="irancell", detail="doesn't work"
    )

    assert outcome.operator_excluded is False
    assert outcome.globally_blocked is False
    assert outcome.already_reported is False


@pytest.mark.asyncio
async def test_five_distinct_reports_same_operator_excludes_only_that_operator() -> None:
    db = _FakeDatabase()
    service = ReportService(db, pipeline_events=_FakeNullEvents())  # type: ignore[arg-type]

    outcome = None
    for user_id in range(1, PER_OPERATOR_THRESHOLD + 1):
        outcome = await service.submit_report(
            config_id="cfg-1", reporter_user_id=user_id, operator_code="irancell", detail="bad"
        )

    assert outcome is not None
    assert outcome.operator_excluded is True
    assert outcome.globally_blocked is False
    assert db.exclusions[("cfg-1", "irancell")] == PER_OPERATOR_THRESHOLD
    assert "cfg-1" not in db.globally_blocked


@pytest.mark.asyncio
async def test_duplicate_report_from_same_user_and_operator_is_idempotent() -> None:
    db = _FakeDatabase()
    service = ReportService(db, pipeline_events=_FakeNullEvents())  # type: ignore[arg-type]

    await service.submit_report(config_id="cfg-1", reporter_user_id=1, operator_code="irancell", detail="bad")
    second = await service.submit_report(
        config_id="cfg-1", reporter_user_id=1, operator_code="irancell", detail="still bad"
    )

    assert second.already_reported is True
    total_row = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM config_reports WHERE config_id=:id", {"id": "cfg-1"}
    )
    assert total_row["n"] == 1


@pytest.mark.asyncio
async def test_ten_distinct_reports_across_operators_blocks_globally() -> None:
    """5 reports on irancell (crosses per-operator threshold) + 4 on mci +
    1 on asiatech ('other') = 10 total distinct reporters -> global block,
    even though no single operator besides irancell crossed its own
    threshold. Mirrors the exact scenario described in the product spec.
    """
    db = _FakeDatabase()
    service = ReportService(db, pipeline_events=_FakeNullEvents())  # type: ignore[arg-type]

    reporter_id = 1
    irancell_fifth_outcome = None
    for i in range(5):
        irancell_fifth_outcome = await service.submit_report(
            config_id="cfg-1", reporter_user_id=reporter_id, operator_code="irancell", detail="bad"
        )
        reporter_id += 1
    for _ in range(4):
        await service.submit_report(
            config_id="cfg-1", reporter_user_id=reporter_id, operator_code="mci", detail="bad"
        )
        reporter_id += 1
    final_outcome = await service.submit_report(
        config_id="cfg-1", reporter_user_id=reporter_id, operator_code="other", detail="bad"
    )

    # The 5th irancell report is the one that actually crosses the
    # per-operator threshold; its own outcome must reflect that.
    assert irancell_fifth_outcome is not None
    assert irancell_fifth_outcome.operator_excluded is True
    assert irancell_fifth_outcome.globally_blocked is False

    # The 10th report overall (on "other") is the one that crosses the
    # global threshold, but "other" itself never reached 5 on its own.
    assert final_outcome.globally_blocked is True
    assert final_outcome.operator_excluded is False

    assert "cfg-1" in db.globally_blocked
    assert db.exclusions[("cfg-1", "irancell")] == PER_OPERATOR_THRESHOLD
    assert db.exclusions.get(("cfg-1", "mci")) is None  # mci alone never reached 5
    assert db.exclusions.get(("cfg-1", "other")) is None  # other alone never reached 5


@pytest.mark.asyncio
async def test_find_config_by_code_strips_country_prefix() -> None:
    db = _FakeDatabase()

    class _LookupDatabase(_FakeDatabase):
        async def fetch_one(self, statement: str, values: dict[str, Any] | None = None) -> dict[str, Any] | None:
            values = values or {}
            if "config_code = :code" in statement:
                if values["code"] == "454":
                    return {"id": "cfg-1", "config_code": "454", "country_code": "IT"}
                return None
            return await super().fetch_one(statement, values)

    service = ReportService(_LookupDatabase(), pipeline_events=_FakeNullEvents())  # type: ignore[arg-type]

    assert (await service.find_config_by_code("#IT454"))["id"] == "cfg-1"
    assert (await service.find_config_by_code("454"))["id"] == "cfg-1"
    assert await service.find_config_by_code("notacode") is None
