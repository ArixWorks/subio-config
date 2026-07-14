"""Local subscription feed cache for delivery inside Iran."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger("subio.sub_store")

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class SubscriptionStore:
    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, token: str) -> Path:
        if not TOKEN_PATTERN.match(token):
            raise ValueError("invalid token")
        return self._root / f"{token}.json"

    def upsert(self, token: str, configs: list[str], *, expires_at: str | None = None) -> None:
        path = self._path(token)
        payload = {
            "token": token,
            "configs": [item for item in configs if isinstance(item, str) and item.strip()],
            "expires_at": expires_at,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def get_body(self, token: str) -> str | None:
        path = self._path(token)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        configs = data.get("configs") or []
        if not isinstance(configs, list):
            return None
        return "\n".join(str(item).strip() for item in configs if str(item).strip())

    def delete(self, token: str) -> None:
        path = self._path(token)
        if path.exists():
            path.unlink()
