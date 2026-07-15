import base64
import hashlib
import hmac
import json
import os
import time

import pytest

from app.security import PayloadCipher, ReplayGuard, verify_signature
from app.xray import outbound_from_uri, run_test


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


def test_ss_sip002_userinfo_is_base64_decoded() -> None:
    """SIP002 shadowsocks URIs encode 'method:password' as base64 userinfo
    (e.g. ss://BASE64(aes-256-gcm:secret)@host:port) rather than embedding the
    plain 'method:password' pair directly. outbound_from_uri must base64-decode
    the userinfo in this case instead of mis-splitting it on ':'.
    """
    userinfo = base64.urlsafe_b64encode(b"aes-256-gcm:s3cr3t-password").decode().rstrip("=")
    uri = f"ss://{userinfo}@example.com:8388?security=none&type=tcp#test"
    outbound = outbound_from_uri(uri)
    assert outbound["protocol"] == "shadowsocks"
    server = outbound["settings"]["servers"][0]
    assert server["address"] == "example.com"
    assert server["port"] == 8388
    assert server["method"] == "aes-256-gcm"
    assert server["password"] == "s3cr3t-password"


def test_ss_legacy_plain_userinfo_still_supported() -> None:
    """Legacy ss://method:password@host:port (no base64) must keep working."""
    uri = "ss://aes-256-gcm:s3cr3t-password@example.com:8388"
    outbound = outbound_from_uri(uri)
    server = outbound["settings"]["servers"][0]
    assert server["method"] == "aes-256-gcm"
    assert server["password"] == "s3cr3t-password"
    assert server["address"] == "example.com"
    assert server["port"] == 8388


@pytest.mark.asyncio
async def test_run_test_returns_unreachable_instead_of_raising_on_dead_proxy(
    tmp_path,
) -> None:
    """When the SOCKS proxy never accepts the outer test_url connection (e.g. the
    upstream node is dead), run_test must resolve to reachable=False rather than
    letting an OSError/TimeoutError escape. A leaked exception here previously
    propagated all the way up through the FastAPI handler as a 502/504, which the
    main-server's ResilientTesterClient could not distinguish from an actual
    tester outage — repeatedly tripping the circuit breaker and stalling the
    entire discovery queue behind a single unreachable "poison pill" config.
    """
    # Fake xray binary: starts, never listens on the SOCKS port, just sleeps.
    fake_binary = tmp_path / "fake-xray.sh"
    fake_binary.write_text("#!/bin/sh\nsleep 30\n")
    fake_binary.chmod(0o755)

    result = await run_test(
        "ss://aes-256-gcm:s3cr3t-password@203.0.113.1:8388",
        str(fake_binary),
        "https://example.invalid/generate_204",
        timeout=2,
        mode="cheap",
    )
    assert result["reachable"] is False
    assert result["health_score"] == 0.0
    assert all(value is False for value in result["checks"].values() if value is not None)
