from app.services.npv_converter import convert_npv_to_v2ray, looks_like_npv


def test_npv_detect() -> None:
    assert looks_like_npv("check npv://abc123 payload")


def test_npv_extracts_vmess_from_json() -> None:
    import base64
    import json

    payload = {
        "add": "1.2.3.4",
        "port": 443,
        "id": "00000000-0000-0000-0000-000000000001",
        "net": "ws",
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    uris = convert_npv_to_v2ray(encoded)
    assert any(uri.startswith("vmess://") for uri in uris)
