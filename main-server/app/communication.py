import asyncio
import enum
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.security import PayloadCipher, signed_headers


class CommunicationUnavailable(RuntimeError):
    pass


class BreakerState(enum.StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_threshold: int = 2
    reset_seconds: float = 45
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    recovery_successes: int = 0
    opened_at: float = 0

    def permit_direct(self) -> bool:
        if self.state is BreakerState.OPEN and time.monotonic() - self.opened_at >= self.reset_seconds:
            self.state = BreakerState.HALF_OPEN
        return self.state is not BreakerState.OPEN

    def success(self) -> None:
        self.failures = 0
        if self.state is BreakerState.HALF_OPEN:
            self.recovery_successes += 1
            if self.recovery_successes >= self.recovery_threshold:
                self.state = BreakerState.CLOSED
                self.recovery_successes = 0

    def failure(self) -> None:
        self.recovery_successes = 0
        self.failures += 1
        if self.state is BreakerState.HALF_OPEN or self.failures >= self.failure_threshold:
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()


class FallbackStore(Protocol):
    async def submit_and_wait(self, job_id: uuid.UUID, envelope: str, timeout: float) -> str: ...


class ResilientTesterClient:
    def __init__(
        self,
        *,
        base_url: str,
        hmac_key: str,
        cipher: PayloadCipher,
        breaker: CircuitBreaker,
        fallback: FallbackStore | None = None,
        timeout: float = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._hmac_key = hmac_key
        self._cipher = cipher
        self._breaker = breaker
        self._fallback = fallback
        self._timeout = min(timeout, 10)

    async def test(self, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid.uuid4()
        envelope = self._cipher.encrypt({"job_id": str(job_id), "payload": payload})
        try:
            async with asyncio.timeout(self._timeout):
                if self._breaker.permit_direct():
                    try:
                        result = await self._direct(envelope)
                        self._breaker.success()
                        return self._cipher.decrypt(result)
                    except (httpx.HTTPError, TimeoutError):
                        self._breaker.failure()
                if self._fallback is None:
                    raise CommunicationUnavailable("tester communication unavailable")
                result = await self._fallback.submit_and_wait(job_id, envelope, self._timeout)
                return self._cipher.decrypt(result)
        except TimeoutError as exc:
            raise CommunicationUnavailable("tester operation exceeded 10 seconds") from exc

    async def probe(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{self._base_url}/health/ready")
                response.raise_for_status()
            self._breaker.success()
            return True
        except httpx.HTTPError:
            self._breaker.failure()
            return False

    async def _direct(self, envelope: str) -> str:
        headers = signed_headers(envelope, self._hmac_key)
        async with httpx.AsyncClient(timeout=min(8, self._timeout)) as client:
            response = await client.post(
                f"{self._base_url}/v1/tests",
                json={"envelope": envelope},
                headers=headers,
            )
            response.raise_for_status()
            result = response.json().get("envelope")
            if not isinstance(result, str):
                raise httpx.DecodingError("tester returned no envelope")
            return result
