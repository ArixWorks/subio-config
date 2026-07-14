from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncConnection, create_async_engine


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url, pool_pre_ping=True, pool_size=10, max_overflow=20
        )

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        async with self.engine.begin() as connection:
            yield connection

    async def ready(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def fetch_one(self, statement: str, values: dict[str, Any] | None = None) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            row = (await connection.execute(text(statement), values or {})).mappings().first()
            return dict(row) if row else None

    async def fetch_all(self, statement: str, values: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (await connection.execute(text(statement), values or {})).mappings().all()
            return [dict(row) for row in rows]

    async def execute(self, statement: str, values: dict[str, Any] | None = None) -> None:
        async with self.connection() as connection:
            await connection.execute(text(statement), values or {})

    async def close(self) -> None:
        await self.engine.dispose()
