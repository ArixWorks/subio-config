import asyncio
import time
from dataclasses import asdict, dataclass

from aiohttp import ClientSession, ClientTimeout
from aiohttp_socks import ProxyConnector


@dataclass
class ProxyHealth:
    uri: str
    priority: int
    successes: int = 0
    failures: int = 0
    latency_ms: int | None = None

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        return self.successes / total * 100 if total else 100

    def public(self) -> dict[str, object]:
        value = asdict(self)
        value["uri"] = self.uri.split("@")[-1]
        value["success_rate"] = self.success_rate
        return value


class ProxyPool:
    def __init__(self, uris: tuple[str, ...]) -> None:
        self._proxies = [ProxyHealth(uri, index) for index, uri in enumerate(uris)]

    async def check(self, proxy: ProxyHealth, target: str) -> bool:
        started = time.monotonic()
        try:
            connector = ProxyConnector.from_url(proxy.uri)
            async with ClientSession(connector=connector) as session:
                async with session.get(target, timeout=ClientTimeout(total=4)) as response:
                    if response.status >= 500:
                        raise RuntimeError("proxy target failed")
            proxy.successes += 1
            proxy.latency_ms = round((time.monotonic() - started) * 1000)
            return True
        except (OSError, RuntimeError, asyncio.TimeoutError):
            proxy.failures += 1
            proxy.latency_ms = None
            return False

    async def best(self, target: str) -> ProxyHealth | None:
        ordered = sorted(
            self._proxies,
            key=lambda item: (-item.success_rate, item.priority, item.latency_ms or 1_000_000),
        )
        for proxy in ordered:
            if await self.check(proxy, target):
                return proxy
        return None

    async def check_all(self, target: str) -> list[dict[str, object]]:
        await asyncio.gather(*(self.check(proxy, target) for proxy in self._proxies))
        return [proxy.public() for proxy in self._proxies]

    def status(self) -> list[dict[str, object]]:
        return [proxy.public() for proxy in self._proxies]

    def reload(self, uris: list[str] | tuple[str, ...]) -> None:
        self._proxies = [ProxyHealth(uri, index) for index, uri in enumerate(uris)]
