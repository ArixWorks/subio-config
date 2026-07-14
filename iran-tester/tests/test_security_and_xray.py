import base64
import hashlib
import hmac
import json
import os
import time

from app.security import PayloadCipher, ReplayGuard, verify_signature
from app.xray import outbound_from_uri


def test_encryption_and_signature() -> None:
    cipher = PayloadCipher(base64.urlsafe_b64encode(os.urandom(32)).decode())
    envelope = cipher.encrypt({"job_id": "abc"})
    timestamp, nonce, key = str(int(time.time())), "unique", "x" * 32
    signature = hmac.new(
        key.encode(), f"{timestamp}.{nonce}.{envelope}".encode(), hashlib.sha256
    ).hexdigest()
    assert verify_signature(
        envelope=envelope, timestamp=timestamp, nonce=nonce, signature=signature, key=key
    )
    assert cipher.decrypt(envelope) == {"job_id": "abc"}


def test_replay_guard_rejects_nonce_reuse() -> None:
    guard = ReplayGuard()
    timestamp = str(int(time.time()))
    assert guard.accept(timestamp, "nonce")
    assert not guard.accept(timestamp, "nonce")


def test_vless_and_vmess_parsing() -> None:
    vless = outbound_from_uri("vless://id@example.com:443?security=tls&sni=example.com&type=ws")
    assert vless["protocol"] == "vless"
    payload = {
        "v": "2",
        "ps": "test",
        "add": "example.com",
        "port": "443",
        "id": "uuid",
        "aid": "0",
        "net": "ws",
        "tls": "tls",
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    assert outbound_from_uri(f"vmess://{encoded}")["protocol"] == "vmess"
