"""Public config distribution and smart naming."""

from __future__ import annotations

from typing import Any

from app.db import Database
from app.services.scoring_service import apply_operator_reports, format_config_name


class DistributionService:
    TOP_N = 10

    def __init__(self, db: Database) -> None:
        self._db = db

    async def top_configs(self, operator: str = "unknown") -> list[dict[str, Any]]:
        async with self._db.engine.connect() as conn:
            from sqlalchemy import text

            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT c.id, c.protocol, c.score, c.latency_ms, c.display_name,
                               c.transport_type, c.operator_scores,
                               COUNT(r.id) FILTER (WHERE r.category='blocked') AS blocked_reports,
                               COUNT(r.id) FILTER (WHERE r.category='slow') AS slow_reports,
                               COUNT(r.id) FILTER (WHERE r.category='disconnect') AS disconnect_reports
                        FROM vpn_configs c
                        LEFT JOIN user_reports r ON r.config_id = c.id AND r.status='pending'
                        WHERE c.scope='public' AND c.is_enabled AND c.score >= 50
                          AND (c.expires_at IS NULL OR c.expires_at > now())
                        GROUP BY c.id
                        ORDER BY c.score DESC, c.latency_ms ASC NULLS LAST
                        LIMIT 50
                        """
                    )
                )
            ).mappings().all()

        ranked: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            operator_scores = row.get("operator_scores") or {}
            base = float(operator_scores.get(operator, row["score"]))
            adjusted = apply_operator_reports(
                base,
                operator,
                {
                    "blocked": int(row["blocked_reports"] or 0),
                    "slow": int(row["slow_reports"] or 0),
                    "disconnect": int(row["disconnect_reports"] or 0),
                },
            )
            location = "DE"
            if row.get("display_name") and "🇺🇸" in str(row["display_name"]):
                location = "US"
            name = row.get("display_name") or format_config_name(
                config_id=index,
                location=location,
                protocol=str(row["protocol"]),
                latency_ms=row.get("latency_ms"),
                score=adjusted,
                transport=row.get("transport_type"),
            )
            ranked.append(
                {
                    "id": str(row["id"]),
                    "name": name,
                    "score": adjusted,
                    "latency_ms": row.get("latency_ms"),
                    "protocol": row["protocol"],
                }
            )
        ranked.sort(key=lambda item: (-float(item["score"]), item["latency_ms"] or 9999))
        return ranked[: self.TOP_N]

    async def update_display_names(self) -> None:
        configs = await self.top_configs()
        for index, item in enumerate(configs, start=1):
            await self._db.execute(
                "UPDATE vpn_configs SET display_name=:name WHERE id=:id",
                {"name": item["name"], "id": item["id"]},
            )
