import asyncio
import base64
import json
import socket
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp
from aiohttp_socks import ProxyConnector


class UnsupportedConfiguration(ValueError):
    pass


SITE_CHECKS = (
    ("instagram", "https://www.instagram.com/"),
    ("youtube", "https://www.youtube.com/generate_204"),
    ("cloudflare", "https://www.cloudflare.com/cdn-cgi/trace"),
    ("telegram", "https://api.telegram.org"),
)


def _stream_settings(query: dict[str, list[str]]) -> dict[str, Any]:
    network = query.get("type", ["tcp"])[0]
    security = query.get("security", ["none"])[0]
    settings: dict[str, Any] = {"network": network, "security": security}
    if security == "tls":
        settings["tlsSettings"] = {
            "serverName": query.get("sni", [""])[0],
            "fingerprint": query.get("fp", ["chrome"])[0],
            "allowInsecure": False,
        }
    elif security == "reality":
        settings["realitySettings"] = {
            "serverName": query.get("sni", [""])[0],
            "fingerprint": query.get("fp", ["chrome"])[0],
            "publicKey": query.get("pbk", [""])[0],
            "shortId": query.get("sid", [""])[0],
            "spiderX": query.get("spx", ["/"])[0],
        }
    if network == "ws":
        settings["wsSettings"] = {
            "path": unquote(query.get("path", ["/"])[0]),
            "headers": {"Host": query.get("host", [""])[0]},
        }
    elif network == "grpc":
        settings["grpcSettings"] = {"serviceName": query.get("serviceName", [""])[0]}
    return settings


def outbound_from_uri(uri: str) -> dict[str, Any]:
    parsed = urlparse(uri)
    query = parse_qs(parsed.query)
    if parsed.scheme in {"vless", "trojan"}:
        if not parsed.hostname or not parsed.port or not parsed.username:
            raise UnsupportedConfiguration("configuration endpoint is incomplete")
        if parsed.scheme == "vless":
            protocol_settings: dict[str, Any] = {
                "vnext": [
                    {
                        "address": parsed.hostname,
                        "port": parsed.port,
                        "users": [
                            {
                                "id": parsed.username,
                                "encryption": query.get("encryption", ["none"])[0],
                                "flow": query.get("flow", [""])[0],
                            }
                        ],
                    }
                ]
            }
        else:
            protocol_settings = {
                "servers": [
                    {
                        "address": parsed.hostname,
                        "port": parsed.port,
                        "password": unquote(parsed.username),
                    }
                ]
            }
        return {
            "protocol": parsed.scheme,
            "settings": protocol_settings,
            "streamSettings": _stream_settings(query),
        }
    if parsed.scheme == "vmess":
        raw = parsed.netloc + parsed.path
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return {
            "protocol": "vmess",
            "settings": {
                "vnext": [
                    {
                        "address": data["add"],
                        "port": int(data["port"]),
                        "users": [{"id": data["id"], "alterId": int(data.get("aid", 0))}],
                    }
                ]
            },
            "streamSettings": {
                "network": data.get("net", "tcp"),
                "security": data.get("tls", "none") or "none",
                "tlsSettings": {"serverName": data.get("sni", data.get("host", ""))},
                "wsSettings": {"path": data.get("path", "/"), "headers": {"Host": data.get("host", "")}},
            },
        }
    if parsed.scheme == "ss":
        if "@" in parsed.netloc:
            method_password, endpoint = parsed.netloc.split("@", 1)
            host, port = endpoint.rsplit(":", 1)
            if ":" in method_password:
                # Legacy/plain form: ss://method:password@host:port
                method, password = method_password.split(":", 1)
            else:
                # SIP002 form: ss://BASE64(method:password)@host:port
                try:
                    padded = method_password + "=" * (-len(method_password) % 4)
                    decoded_userinfo = base64.urlsafe_b64decode(padded).decode()
                    method, password = decoded_userinfo.split(":", 1)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise UnsupportedConfiguration("invalid shadowsocks userinfo") from exc
        else:
            decoded = base64.urlsafe_b64decode(parsed.netloc + "=" * (-len(parsed.netloc) % 4)).decode()
            method, password, host, port = decoded.split(":", 3)
        return {
            "protocol": "shadowsocks",
            "settings": {
                "servers": [
                    {
                        "address": host,
                        "port": int(port),
                        "method": method,
                        "password": password,
                    }
                ]
            },
        }
    raise UnsupportedConfiguration(f"unsupported protocol: {parsed.scheme}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _probe_one(session: aiohttp.ClientSession, url: str, per_site_timeout: float) -> bool:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=per_site_timeout)) as response:
            return response.status < 500
    except (OSError, asyncio.TimeoutError, aiohttp.ClientError):
        return False


async def _check_sites(session: aiohttp.ClientSession, per_site_timeout: float) -> dict[str, bool]:
    # Run all probes concurrently so total wall time is ~per_site_timeout, not
    # len(SITE_CHECKS) * per_site_timeout (which previously blew the outer
    # operation deadline and caused spurious 504/CommunicationUnavailable).
    names = [name for name, _ in SITE_CHECKS]
    results = await asyncio.gather(
        *(_probe_one(session, url, per_site_timeout) for _, url in SITE_CHECKS)
    )
    return dict(zip(names, results))


async def _speed_test(session: aiohttp.ClientSession, bytes_count: int = 1048576) -> float | None:
    # Prefer tiny probes on Iran to save download quota; full 1MiB only for mode=full.
    url = f"https://speed.cloudflare.com/__down?bytes={bytes_count}"
    started = time.monotonic()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as response:
            data = await response.read()
            elapsed = max(time.monotonic() - started, 0.001)
            return round((len(data) * 8) / (elapsed * 1_000_000), 2)
    except (OSError, asyncio.TimeoutError, aiohttp.ClientError):
        return None


CHEAP_SITES = (
    ("cloudflare", "https://www.cloudflare.com/cdn-cgi/trace"),
    ("youtube", "https://www.youtube.com/generate_204"),
)


async def _check_sites_named(
    session: aiohttp.ClientSession,
    sites: tuple[tuple[str, str], ...],
    per_site_timeout: float,
) -> dict[str, bool]:
    names = [name for name, _ in sites]
    results = await asyncio.gather(
        *(_probe_one(session, url, per_site_timeout) for _, url in sites)
    )
    return dict(zip(names, results))


def compute_health_score(result: dict[str, Any]) -> float:
    if not result.get("reachable"):
        return 0.0
    score = 40.0
    latency = int(result.get("latency_ms") or 999)
    score += max(0, 25 - latency / 4)
    weights = {"instagram": 15, "youtube": 15, "cloudflare": 10, "telegram": 20, "http": 10}
    checks = result.get("checks") or {}
    for site, weight in weights.items():
        if checks.get(site):
            score += weight
    speed = result.get("download_mbps")
    if speed is not None:
        score += min(15, float(speed) * 3)
    # Cheap mode intentionally omits heavy downloads; keep a usable floor when alive.
    if result.get("mode") == "cheap" and score < 55:
        score = max(score, 55.0)
    return round(min(100, max(0, score)), 2)


async def run_test(
    uri: str,
    xray_binary: str,
    test_url: str,
    timeout: float = 8,
    mode: str = "full",
) -> dict[str, Any]:
    """Run Iran-side connectivity test.

    mode=cheap → handshake + 1-2 tiny checks (save Iran download traffic)
    mode=full  → multi-site + speed sample (new configs / dead→alive promote)
    """
    mode = "cheap" if mode == "cheap" else "full"
    started = time.monotonic()
    port = _free_port()
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": False},
            }
        ],
        "outbounds": [outbound_from_uri(uri)],
    }
    with tempfile.TemporaryDirectory(prefix="subio-") as directory:
        path = Path(directory, "config.json")
        path.write_text(json.dumps(config), encoding="utf-8")
        process = await asyncio.create_subprocess_exec(
            xray_binary,
            "run",
            "-config",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.sleep(0.35)
            if process.returncode is not None:
                error = (await process.stderr.read()).decode(errors="replace")[-500:]
                raise RuntimeError(f"xray rejected configuration: {error}")
            connector = ProxyConnector.from_url(f"socks5://127.0.0.1:{port}")
            per_site = max(1.2, min(3.0, timeout / 4))
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(test_url, timeout=aiohttp.ClientTimeout(total=per_site)) as response:
                    reachable = response.status < 500
                if mode == "cheap":
                    checks = await _check_sites_named(session, CHEAP_SITES, per_site)
                    # Tiny 16KiB probe (~0.016MB) instead of 1MiB speed test.
                    download_mbps = await _speed_test(session, bytes_count=16_384) if reachable else None
                else:
                    checks = await _check_sites(session, per_site)
                    download_mbps = await _speed_test(session, bytes_count=1_048_576) if reachable else None
            latency = round((time.monotonic() - started) * 1000)
            checks["http"] = reachable
            payload = {
                "reachable": reachable,
                "latency_ms": latency,
                "download_mbps": download_mbps,
                "checks": checks,
                "mode": mode,
            }
            payload["health_score"] = compute_health_score(payload)
            return payload
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=1)
                except TimeoutError:
                    process.kill()
                    await process.wait()
