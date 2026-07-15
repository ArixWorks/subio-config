"""Problem-report intake, per-operator/global thresholds, and per-user
auto-replacement for the public subscription feed.

Business rules (as specified by product):
- A single report never disables a config on its own.
- Once >=5 *distinct* users on the *same* operator report a config, it is
  excluded from that operator's feeds only (config_operator_exclusions),
  while remaining fully visible/healthy for every other operator.
- Once the *total* distinct-user report count (summed across every
  operator) reaches >=10, the config is globally blocked
  (vpn_configs.is_globally_blocked) — removed from every public feed even
  if the Iran tester still considers it healthy.
- Whenever a config becomes excluded/blocked for a given user's feed, that
  user's public feed is immediately resynced so a replacement config takes
  its place without waiting for the next scheduled sync.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.db import Database
from app.services.pipeline_events import PipelineEventService

logger = logging.getLogger("subio.report_service")

PER_OPERATOR_THRESHOLD = 5
GLOBAL_THRESHOLD = 10


@dataclass(frozen=True)
class ReportOutcome:
    config_id: str
    config_code: str
    operator_excluded: bool
    globally_blocked: bool
    already_reported: bool


class ReportService:
    def __init__(self, db: Database, pipeline_events: PipelineEventService | None = None) -> None:
        self._db = db
        self._events = pipeline_events or PipelineEventService(db)

    async def find_config_by_code(self, config_code: str) -> dict[str, Any] | None:
        normalized = config_code.strip().upper().lstrip("#")
        # Stored config_code is only the numeric suffix (see migration 006);
        # the user-facing/report token is "{COUNTRY}{CODE}" e.g. "IT454".
        # Strip a leading 2-letter country prefix if present so both "#454"
        # and "#IT454" resolve to the same row.
        numeric_part = normalized
        if len(normalized) > 2 and normalized[:2].isalpha():
            numeric_part = normalized[2:]
        if not numeric_part.isdigit():
            return None
        return await self._db.fetch_one(
            """
            SELECT id::text AS id, config_code, country_code, display_name, is_globally_blocked
            FROM vpn_configs WHERE config_code = :code
            """,
            {"code": numeric_part},
        )

    async def submit_report(
        self,
        *,
        config_id: str,
        reporter_user_id: int,
        operator_code: str,
        detail: str | None,
    ) -> ReportOutcome:
        config = await self._db.fetch_one(
            "SELECT config_code FROM vpn_configs WHERE id=:id",
            {"id": config_id},
        )
        config_code = str((config or {}).get("config_code") or "")

        async with self._db.connection() as conn:
            from sqlalchemy import text

            inserted = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO config_reports(config_id, reporter_user_id, operator_code, detail)
                        VALUES (:config_id, :reporter, :operator, :detail)
                        ON CONFLICT (config_id, reporter_user_id, operator_code) DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "config_id": config_id,
                        "reporter": reporter_user_id,
                        "operator": operator_code,
                        "detail": detail,
                    },
                )
            ).first()
        already_reported = inserted is None

        operator_count_row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM config_reports WHERE config_id=:id AND operator_code=:operator",
            {"id": config_id, "operator": operator_code},
        )
        operator_count = int((operator_count_row or {}).get("n") or 0)

        total_count_row = await self._db.fetch_one(
            "SELECT COUNT(*) AS n FROM config_reports WHERE config_id=:id",
            {"id": config_id},
        )
        total_count = int((total_count_row or {}).get("n") or 0)

        operator_excluded = False
        if operator_count >= PER_OPERATOR_THRESHOLD:
            operator_excluded = await self._exclude_for_operator(
                config_id=config_id,
                config_code=config_code,
                operator_code=operator_code,
                report_count=operator_count,
            )

        globally_blocked = False
        if total_count >= GLOBAL_THRESHOLD:
            globally_blocked = await self._block_globally(
                config_id=config_id, config_code=config_code, total_count=total_count
            )

        if not already_reported:
            await self._events.emit(
                stage="reported",
                status="warning",
                config_id=config_id,
                config_code=config_code,
                message=(
                    f"گزارش خرابی ثبت شد (اپراتور {operator_code}: {operator_count}, "
                    f"مجموع: {total_count})"
                ),
                metadata={
                    "operator_code": operator_code,
                    "operator_count": operator_count,
                    "total_count": total_count,
                },
            )

        return ReportOutcome(
            config_id=config_id,
            config_code=config_code,
            operator_excluded=operator_excluded,
            globally_blocked=globally_blocked,
            already_reported=already_reported,
        )

    async def _exclude_for_operator(
        self, *, config_id: str, config_code: str, operator_code: str, report_count: int
    ) -> bool:
        async with self._db.connection() as conn:
            from sqlalchemy import text

            result = await conn.execute(
                text(
                    """
                    INSERT INTO config_operator_exclusions(config_id, operator_code, report_count)
                    VALUES (:config_id, :operator, :count)
                    ON CONFLICT (config_id, operator_code)
                    DO UPDATE SET report_count=:count
                    WHERE config_operator_exclusions.report_count < :count
                    RETURNING config_id
                    """
                ),
                {"config_id": config_id, "operator": operator_code, "count": report_count},
            )
            newly_excluded = result.first() is not None
        if newly_excluded:
            logger.info(
                "config_excluded_for_operator",
                extra={"config_id": config_id, "operator": operator_code, "report_count": report_count},
            )
            await self._events.emit(
                stage="operator_excluded",
                status="error",
                config_id=config_id,
                config_code=config_code,
                message=f"کانفیگ برای اپراتور {operator_code} حذف شد ({report_count} گزارش)",
                metadata={"operator_code": operator_code, "report_count": report_count},
            )
        return True

    async def _block_globally(self, *, config_id: str, config_code: str, total_count: int) -> bool:
        row = await self._db.fetch_one(
            "SELECT is_globally_blocked FROM vpn_configs WHERE id=:id",
            {"id": config_id},
        )
        if row and bool(row.get("is_globally_blocked")):
            return True
        await self._db.execute(
            "UPDATE vpn_configs SET is_globally_blocked=TRUE WHERE id=:id",
            {"id": config_id},
        )
        logger.info(
            "config_globally_blocked", extra={"config_id": config_id, "total_reports": total_count}
        )
        await self._events.emit(
            stage="globally_blocked",
            status="error",
            config_id=config_id,
            config_code=config_code,
            message=f"کانفیگ به‌طور کامل از تمام ساب‌ها حذف شد ({total_count} گزارش مجموع)",
            metadata={"total_count": total_count},
        )
        return True

    async def mark_user_feed_replacement(self, *, user_id: int, config_id: str) -> None:
        """Forces the reporting user's own feed to stop serving this config
        immediately (before the operator/global thresholds are necessarily
        crossed) and be resynced with a replacement on the next sync pass —
        satisfying "for that specific user" auto-replacement even on a
        single, first-time report.
        """
        await self._db.execute(
            """
            UPDATE public_feeds
            SET excluded_config_ids = array_append(
                  excluded_config_ids,
                  CAST(:config_id AS uuid)
                ),
                updated_at = now()
            WHERE user_id = :user_id
              AND NOT (CAST(:config_id AS uuid) = ANY(excluded_config_ids))
            """,
            {"user_id": user_id, "config_id": config_id},
        )
