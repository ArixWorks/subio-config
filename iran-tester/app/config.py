from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: str = "production"
    log_level: str = "INFO"
    internal_hmac_key: str = Field(min_length=32)
    payload_encryption_key: str
    max_operation_seconds: float = Field(default=18, ge=1, le=30)
    xray_binary: str = "/usr/local/bin/xray"
    xray_test_url: str = "https://cp.cloudflare.com/generate_204"
    socks_proxies: str = ""
    subscription_store_dir: str = "/data/subs"
    arvan_s3_endpoint: str | None = None
    arvan_s3_region: str = "ir-thr-at1"
    arvan_s3_bucket: str | None = None
    arvan_s3_access_key: str | None = None
    arvan_s3_secret_key: str | None = None
    s3_poll_seconds: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def validate_s3(self) -> "Settings":
        values = (
            self.arvan_s3_endpoint,
            self.arvan_s3_bucket,
            self.arvan_s3_access_key,
            self.arvan_s3_secret_key,
        )
        if any(values) and not all(values):
            raise ValueError("all ARVAN_S3 settings are required together")
        return self

    @property
    def proxy_uris(self) -> tuple[str, ...]:
        from urllib.parse import urlparse, quote
        import base64

        result: list[str] = []
        for item in self.socks_proxies.split(","):
            raw = item.strip()
            if not raw:
                continue
            if "# " in raw or raw.endswith("#"):
                raw = raw.split("#", 1)[0]
            if "#" in raw and "@" in raw:
                raw = raw.split("#", 1)[0]
            parsed = urlparse(raw if "://" in raw else f"socks5://{raw}")
            scheme = "socks5" if parsed.scheme in {"socks", "socks5", ""} else parsed.scheme
            username = parsed.username
            password = parsed.password
            if username and password is None:
                try:
                    decoded = base64.b64decode(username + "==").decode()
                    if ":" in decoded:
                        username, password = decoded.split(":", 1)
                except Exception:
                    pass
            auth = ""
            if username:
                auth = f"{quote(username, safe='')}:{quote(password or '', safe='')}@"
            result.append(f"{scheme}://{auth}{parsed.hostname}:{parsed.port}")
        return tuple(result)


@lru_cache
def get_settings() -> Settings:
    return Settings()
