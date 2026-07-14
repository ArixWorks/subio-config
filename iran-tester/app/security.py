import base64
import hashlib
import hmac
import json
import time
from collections import OrderedDict
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _decode_urlsafe_b64(value: str) -> bytes:
    """Decode URL-safe base64, accepting keys with or without '=' padding."""
    cleaned = "".join(value.split())
    cleaned += "=" * (-len(cleaned) % 4)
    return base64.urlsafe_b64decode(cleaned.encode("ascii"))


class ReplayGuard:
    def __init__(self, capacity: int = 10_000, max_age_seconds: int = 30) -> None:
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._capacity = capacity
        self._max_age = max_age_seconds

    def accept(self, timestamp: str, nonce: str) -> bool:
        try:
            sent_at = int(timestamp)
        except ValueError:
            return False
        now = time.time()
        if abs(now - sent_at) > self._max_age or nonce in self._seen:
            return False
        self._seen[nonce] = now
        while len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return True


class PayloadCipher:
    def __init__(self, encoded_key: str) -> None:
        try:
            key = _decode_urlsafe_b64(encoded_key.strip().strip("\"'"))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid encryption key encoding") from exc
        if len(key) != 32:
            raise ValueError("encryption key must decode to 32 bytes")
        self._cipher = AESGCM(key)

    def decrypt(self, envelope: str, aad: bytes = b"subio:v1") -> dict[str, Any]:
        raw = base64.urlsafe_b64decode(envelope)
        value = json.loads(self._cipher.decrypt(raw[:12], raw[12:], aad))
        if not isinstance(value, dict):
            raise ValueError("payload must be an object")
        return value

    def encrypt(self, value: dict[str, Any], aad: bytes = b"subio:v1") -> str:
        import secrets

        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(nonce + self._cipher.encrypt(nonce, plaintext, aad)).decode()


def verify_signature(
    *, envelope: str, timestamp: str, nonce: str, signature: str, key: str
) -> bool:
    message = f"{timestamp}.{nonce}.{envelope}".encode()
    expected = hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.encode(), expected.encode())
