# Architecture and security

## Trust boundaries

`main-server` is the system of record. Telegram updates, admin requests, panel credentials, subscriptions and user records terminate there. `iran-tester` accepts only bounded test jobs and never receives panel or Telegram credentials.

Direct requests use `X-SubIO-Timestamp`, `X-SubIO-Nonce` and an HMAC-SHA256 signature over timestamp, nonce and ciphertext. Payloads are AES-256-GCM envelopes with authenticated metadata. The tester rejects stale timestamps and reused nonces. Put both APIs behind TLS; encryption does not replace transport authentication.

When direct communication fails, the same encrypted envelope is written to random S3 keys under `pending/`. Results use `results/`. Configure a 30–60 minute bucket lifecycle and an IAM identity restricted to that bucket and prefixes. Object IDs are idempotency keys.

## Runtime topology

Main Compose runs:

- `api`: FastAPI health, admin, tester dispatch and subscription endpoints
- `bot`: aiogram long-polling process
- `worker`: ARQ scheduled cleanup and direct-health reconciliation
- PostgreSQL 16 and Redis 7

Tester Compose runs one stateless API service. It executes xray-core in an isolated temporary directory, enforces resource and process time limits, checks SOCKS endpoints by priority/health, polls S3 when configured, and publishes Prometheus metrics.

## Communication state machine

The breaker starts closed (direct). Three direct failures open it and select S3. After the reset interval it becomes half-open. Two successful probes close it. Manual mode may force direct or S3 but should be used only during incidents.

Every tester operation is wrapped by an absolute 10-second timeout. Nested network calls use smaller budgets so cleanup and response serialization fit inside that limit.

## Admin and rate limiting

Admin APIs require a constant-time checked bearer token. The deployment must terminate TLS and restrict admin paths by VPN or IP allow-list. Redis-backed fixed-window rate limiting protects write and dispatch paths. Rotate admin, HMAC and encryption keys independently.

## UI assets

Bot-facing emoji and style values are records in `ui_assets`, cached in Redis by key and language. Handlers do not embed interface emoji. Normal Unicode and Telegram custom emoji IDs are represented explicitly.

## Observability

Services emit JSON logs without payloads, proxy passwords, tokens or configuration URLs. Prometheus exposes request, tester timeout, communication mode, S3 latency and SOCKS-health metrics. Health endpoints distinguish process liveness from dependency readiness.
