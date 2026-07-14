"""Convert VPNDecryptorBot JSON profiles into shareable v2ray URIs."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import quote


def _q(value: str | None) -> str:
    return quote(str(value or ""), safe="")


def profile_to_uri(item: dict[str, Any]) -> str | None:
    profile = item.get("v2rayProfile")
    if not isinstance(profile, dict):
        return None

    # Prefer reconstructing from structured fields; fall back to outbound in v2rayJson.
    server = str(profile.get("server") or "")
    port = str(profile.get("serverPort") or "")
    uuid = str(profile.get("password") or "")
    remarks = str(profile.get("remarks") or item.get("name") or "SubIO")
    network = str(profile.get("network") or "tcp")
    security = str(profile.get("security") or "none")
    if not server or not port or not uuid:
        return _from_v2ray_json(profile.get("v2rayJson"), remarks)

    params: list[str] = [
        "encryption=none",
        f"type={_q(network)}",
        f"security={_q(security)}",
    ]
    if profile.get("host"):
        params.append(f"host={_q(profile.get('host'))}")
    if profile.get("path"):
        params.append(f"path={_q(profile.get('path'))}")
    if profile.get("sni"):
        params.append(f"sni={_q(profile.get('sni'))}")
    if profile.get("fingerPrint"):
        params.append(f"fp={_q(profile.get('fingerPrint'))}")
    if profile.get("alpn"):
        alpn = profile.get("alpn")
        if isinstance(alpn, list):
            alpn = ",".join(str(x) for x in alpn)
        params.append(f"alpn={_q(str(alpn))}")
    if profile.get("flow"):
        params.append(f"flow={_q(profile.get('flow'))}")
    if profile.get("serviceName"):
        params.append(f"serviceName={_q(profile.get('serviceName'))}")
    if profile.get("mode"):
        params.append(f"mode={_q(profile.get('mode'))}")
    if security == "reality":
        if profile.get("publicKey"):
            params.append(f"pbk={_q(profile.get('publicKey'))}")
        if profile.get("shortId"):
            params.append(f"sid={_q(profile.get('shortId'))}")
        if profile.get("spiderX"):
            params.append(f"spx={_q(profile.get('spiderX'))}")

    return f"vless://{uuid}@{server}:{port}?{'&'.join(params)}#{_q(remarks)}"


def _from_v2ray_json(raw: Any, remarks: str) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    for outbound in data.get("outbounds") or []:
        if outbound.get("protocol") != "vless":
            continue
        vnext = ((outbound.get("settings") or {}).get("vnext") or [{}])[0]
        user = ((vnext.get("users") or [{}])[0])
        stream = outbound.get("streamSettings") or {}
        server = vnext.get("address")
        port = vnext.get("port")
        uuid = user.get("id")
        if not server or not port or not uuid:
            continue
        network = stream.get("network") or "tcp"
        security = stream.get("security") or "none"
        params = [
            f"encryption={_q(user.get('encryption') or 'none')}",
            f"type={_q(network)}",
            f"security={_q(security)}",
        ]
        ws = stream.get("wsSettings") or {}
        if ws.get("path"):
            params.append(f"path={_q(ws.get('path'))}")
        host = ((ws.get("headers") or {}).get("Host"))
        if host:
            params.append(f"host={_q(host)}")
        tls = stream.get("tlsSettings") or {}
        reality = stream.get("realitySettings") or {}
        if tls.get("serverName"):
            params.append(f"sni={_q(tls.get('serverName'))}")
        if tls.get("fingerprint"):
            params.append(f"fp={_q(tls.get('fingerprint'))}")
        if reality.get("serverName"):
            params.append(f"sni={_q(reality.get('serverName'))}")
        if reality.get("fingerprint"):
            params.append(f"fp={_q(reality.get('fingerprint'))}")
        if reality.get("publicKey"):
            params.append(f"pbk={_q(reality.get('publicKey'))}")
        if reality.get("shortId"):
            params.append(f"sid={_q(reality.get('shortId'))}")
        if reality.get("spiderX"):
            params.append(f"spx={_q(reality.get('spiderX'))}")
        grpc = stream.get("grpcSettings") or {}
        if grpc.get("serviceName"):
            params.append(f"serviceName={_q(grpc.get('serviceName'))}")
        return f"vless://{uuid}@{server}:{port}?{'&'.join(params)}#{_q(remarks)}"
    return None


def decryptor_json_to_uris(payload: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return found
    if not isinstance(payload, dict):
        return found
    configs = payload.get("configs")
    if not isinstance(configs, list):
        return found
    for item in configs:
        if not isinstance(item, dict):
            continue
        uri = profile_to_uri(item)
        if uri:
            found.add(uri)
        # also accept ready-made share links if present
        for key in ("uri", "link", "shareLink", "config"):
            value = item.get(key)
            if isinstance(value, str) and "://" in value:
                found.add(value.strip())
    return found


def vmess_like_to_uri(add: str, port: int, uid: str, **extra: Any) -> str:
    payload = {
        "v": "2",
        "ps": extra.get("ps", "SubIO"),
        "add": add,
        "port": str(port),
        "id": uid,
        "aid": "0",
        "net": extra.get("net", "tcp"),
        "type": "none",
        "host": extra.get("host", ""),
        "path": extra.get("path", ""),
        "tls": extra.get("tls", ""),
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return f"vmess://{encoded.rstrip('=')}"
