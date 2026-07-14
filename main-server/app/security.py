import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _decode_urlsafe_b64(value: str) -> bytes:
    """Decode URL-safe base64, accepting keys with or without '=' padding."""
    cleaned = "".join(value.split())
    cleaned += "=" * (-len(cleaned) % 4)
    return base64.urlsafe_b64decode(cleaned.encode("ascii"))


class PayloadCipher:
    def __init__(self, encoded_key: str) -> None:
        try:
            key = _decode_urlsafe_b64(encoded_key.strip().strip("\"'"))
        except (ValueError, TypeError) as exc:
            raise ValueError("PAYLOAD_ENCRYPTION_KEY must be URL-safe base64") from exc
        if len(key) != 32:
            raise ValueError("PAYLOAD_ENCRYPTION_KEY must decode to 32 bytes")
        self._cipher = AESGCM(key)

    def encrypt(self, value: dict[str, Any], *, aad: bytes = b"subio:v1") -> str:
        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
        return base64.urlsafe_b64encode(nonce + self._cipher.encrypt(nonce, plaintext, aad)).decode()

    def decrypt(self, envelope: str, *, aad: bytes = b"subio:v1") -> dict[str, Any]:
        raw = base64.urlsafe_b64decode(envelope.encode())
        if len(raw) < 29:
            raise ValueError("invalid encrypted envelope")
        value = json.loads(self._cipher.decrypt(raw[:12], raw[12:], aad))
        if not isinstance(value, dict):
            raise ValueError("payload must be an object")
        return value


def signed_headers(envelope: str, key: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(18)
    message = f"{timestamp}.{nonce}.{envelope}".encode()
    signature = hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
    return {
        "X-SubIO-Timestamp": timestamp,
        "X-SubIO-Nonce": nonce,
        "X-SubIO-Signature": signature,
    }


def constant_time_token(provided: str | None, expected: str) -> bool:
    return provided is not None and hmac.compare_digest(provided.encode(), expected.encode())
