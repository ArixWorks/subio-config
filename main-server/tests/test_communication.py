import base64
import os

from app.communication import BreakerState, CircuitBreaker
from app.security import PayloadCipher


def test_cipher_round_trip_and_random_nonce() -> None:
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    cipher = PayloadCipher(key)
    first = cipher.encrypt({"job": "one"})
    second = cipher.encrypt({"job": "one"})
    assert first != second
    assert cipher.decrypt(first) == {"job": "one"}


def test_breaker_opens_and_requires_recovery_threshold() -> None:
    breaker = CircuitBreaker(failure_threshold=3, recovery_threshold=2, reset_seconds=0)
    breaker.failure()
    breaker.failure()
    assert breaker.state is BreakerState.CLOSED
    breaker.failure()
    assert breaker.state is BreakerState.OPEN
    assert breaker.permit_direct()
    assert breaker.state is BreakerState.HALF_OPEN
    breaker.success()
    assert breaker.state is BreakerState.HALF_OPEN
    breaker.success()
    assert breaker.state is BreakerState.CLOSED
