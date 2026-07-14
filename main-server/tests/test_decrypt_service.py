"""Tests for decrypt helpers."""

from app.services.decrypt_service import extract_plain_configs, looks_encrypted, _configs_from_json


def test_extract_plain_configs() -> None:
    text = "link: vless://uuid@1.2.3.4:443?security=reality#test"
    configs = extract_plain_configs(text)
    assert len(configs) == 1
    assert configs.pop().startswith("vless://")


def test_looks_encrypted_markers() -> None:
    assert looks_encrypted("sub://encryptedpayload")
    assert not looks_encrypted("vless://uuid@1.2.3.4:443")


def test_configs_from_json() -> None:
    payload = {"configs": ["vless://a@1.1.1.1:443", "vmess://abc"]}
    found = _configs_from_json(payload)
    assert len(found) >= 1
