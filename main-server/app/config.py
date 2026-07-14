from functools import lru_cache

from pydantic import Field, HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: str = "production"
    log_level: str = "INFO"
    bot_token: str = Field(min_length=20)
    admin_telegram_ids: str = ""
    admin_token: str = Field(min_length=32)
    database_url: str
    redis_url: str
    tester_base_url: HttpUrl
    internal_hmac_key: str = Field(min_length=32)
    payload_encryption_key: str
    tester_timeout_seconds: float = Field(default=10, ge=1, le=10)
    breaker_failure_threshold: int = Field(default=3, ge=1, le=10)
    breaker_recovery_successes: int = Field(default=2, ge=1, le=10)
    breaker_reset_seconds: int = Field(default=45, ge=30, le=300)
    arvan_s3_endpoint: str | None = None
    arvan_s3_region: str = "ir-thr-at1"
    arvan_s3_bucket: str | None = None
    arvan_s3_access_key: str | None = None
    arvan_s3_secret_key: str | None = None
    telethon_api_id: int | None = None
    telethon_api_hash: str | None = None
    telethon_session: str | None = None
    telethon_source_chats: str = ""
    public_base_url: HttpUrl = Field(default="https://api.example.com")

    # Vercel AI Gateway
    ai_enabled: bool = True
    vercel_ai_gateway_api_key: str | None = None
    ai_gateway_base_url: str = "https://ai-gateway.vercel.sh/v1"
    ai_model_sol: str = "openai/gpt-5.6-sol"
    ai_model_luna: str = "openai/gpt-5.6-luna"

    # Retest cadence (Iran bandwidth-aware)
    retest_healthy_interval_seconds: int = Field(default=10, ge=5, le=120)
    retest_healthy_batch: int = Field(default=8, ge=1, le=50)
    retest_dead_interval_seconds: int = Field(default=180, ge=30, le=3600)
    retest_dead_batch: int = Field(default=5, ge=1, le=30)
    retest_demote_on_first_fail: bool = True

    @field_validator("admin_telegram_ids")
    @classmethod
    def validate_admin_ids(cls, value: str) -> str:
        if value and any(not item.strip().isdigit() for item in value.split(",")):
            raise ValueError("ADMIN_TELEGRAM_IDS must be comma-separated integers")
        return value

    @model_validator(mode="after")
    def validate_s3_group(self) -> "Settings":
        values = (
            self.arvan_s3_endpoint,
            self.arvan_s3_bucket,
            self.arvan_s3_access_key,
            self.arvan_s3_secret_key,
        )
        if any(values) and not all(values):
            raise ValueError("all ARVAN_S3 variables must be set together")
        return self

    @property
    def admin_ids(self) -> frozenset[int]:
        return frozenset(int(item) for item in self.admin_telegram_ids.split(",") if item)

    @property
    def s3_enabled(self) -> bool:
        return bool(self.arvan_s3_endpoint)

    @property
    def source_chats(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.telethon_source_chats.split(",") if item.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
