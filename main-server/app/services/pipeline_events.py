"""Live pipeline event log for the admin panel.

Every meaningful step a config goes through — ingestion on the foreign VPS,
dispatch to the Iran tester, country/name resolution, site testing, and the
promote/demote outcome — is recorded here as a lightweight row. The admin
panel polls `/admin/pipeline/events` to render a live feed without needing a
websocket.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db import Database

logger = logging.getLogger("subio.pipeline_events")

Stage = str  # "ingested" | "dispatched" | "country_resolved" | "tested" | "promoted" | "demoted" | "named"
Status = str  # "info" | "success" | "warning" | "error"


class PipelineEventService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def emit(
        self,
        *,
        stage: Stage,
        status: Status = "info",
        config_id: str | None = None,
        config_code: str | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        import json

        try:
            await self._db.execute(
                """
                INSERT INTO pipeline_events(config_id, config_code, stage, status, message, metadata)
                VALUES (:config_id, :config_code, :stage, :status, :message, CAST(:metadata AS jsonb))
                """,
                {
                    "config_id": config_id,
                    "config_code": config_code,
                    "stage": stage,
                    "status": status,
                    "message": message,
                    "metadata": json.dumps(metadata or {}),
                },
            )
        except Exception:
            # Observability must never break the actual pipeline it observes.
            logger.exception("pipeline_event_emit_failed", extra={"stage": stage})

    async def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        return await self._db.fetch_all(
            """
            SELECT id, config_id::text AS config_id, config_code, stage, status, message,
                   metadata, created_at
            FROM pipeline_events
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """,
            {"limit": limit},
        )

    async def for_config(self, config_id: str, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        return await self._db.fetch_all(
            """
            SELECT id, config_id::text AS config_id, config_code, stage, status, message,
                   metadata, created_at
            FROM pipeline_events
            WHERE config_id = :config_id
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """,
            {"config_id": config_id, "limit": limit},
        )

    async def prune(self, older_than_days: int = 14) -> int:
        async with self._db.connection() as conn:
            from sqlalchemy import text

            result = await conn.execute(
                text(
                    "DELETE FROM pipeline_events WHERE created_at < now() - (:days || ' days')::interval"
                ),
                {"days": older_than_days},
            )
            return result.rowcount or 0
