from app.services.decryptor_json import decryptor_json_to_uris, profile_to_uri


def test_profile_ws_tls_to_vless() -> None:
    item = {
        "name": "@test",
        "v2rayProfile": {
            "remarks": "@test",
            "server": "example.com",
            "serverPort": "443",
            "password": "f1f47653-d57b-5917-fa47-c80823a8b7f3",
            "network": "ws",
            "host": "example.com",
            "path": "/ws/abc",
            "security": "tls",
            "sni": "example.com",
            "fingerPrint": "chrome",
            "alpn": "http/1.1",
        },
    }
    uri = profile_to_uri(item)
    assert uri is not None
    assert uri.startswith("vless://f1f47653-d57b-5917-fa47-c80823a8b7f3@example.com:443?")
    assert "type=ws" in uri
    assert "security=tls" in uri
    assert "path=%2Fws%2Fabc" in uri


def test_decryptor_json_multiple_configs() -> None:
    payload = {
        "version": 1,
        "configs": [
            {
                "name": "a",
                "v2rayProfile": {
                    "server": "1.1.1.1",
                    "serverPort": "443",
                    "password": "11111111-1111-1111-1111-111111111111",
                    "network": "tcp",
                    "security": "none",
                    "remarks": "a",
                },
            },
            {
                "name": "b",
                "v2rayProfile": {
                    "server": "2.2.2.2",
                    "serverPort": "8443",
                    "password": "22222222-2222-2222-2222-222222222222",
                    "network": "grpc",
                    "security": "reality",
                    "serviceName": "svc",
                    "publicKey": "pbk",
                    "shortId": "sid",
                    "sni": "play.google.com",
                    "fingerPrint": "chrome",
                    "remarks": "b",
                },
            },
        ],
    }
    uris = decryptor_json_to_uris(payload)
    assert len(uris) == 2
    assert any("pbk=pbk" in u and "security=reality" in u for u in uris)
