# Data model and operations

## Core schema

The main service creates tables for users/referrals, plans, subscriptions and volume accounting, public/private configurations, panel definitions, test jobs/results, user reports, SOCKS metadata, communication state/switch logs, and localized UI assets. Passwords and panel secrets are stored only as AES-GCM ciphertext. Configuration URLs are never logged.

Public configurations are normalized, deduplicated by SHA-256 fingerprint, tested, scored and distributed by health score. Private configurations belong to one subscription and carry expiry and byte limits. Reports lower a configuration's score and enter the tester queue; repeated failures disable distribution. Expired subscriptions and stale jobs are removed by scheduled ARQ tasks.

## Subscription edge (Iran)

User-facing subscription URLs must resolve to the Iran VPS (for example `https://config.ir/sub/{token}`). The main worker pushes active subscription feeds to `iran-tester` over Direct HMAC (`POST /v1/subscription-sync`) and falls back to S3 objects under `subs/` when Direct is unavailable. Clients inside Iran keep receiving config updates from the local edge cache even if connectivity to the foreign main server is interrupted.

The schema is initialized from `main-server/migrations/001_initial.sql`.

## 3x-ui panels

Create one panel row per endpoint through the authenticated admin API. Use a dedicated least-privilege panel account and HTTPS with a valid certificate. Panel adapters belong behind the `PanelGateway` interface; deployment-specific py3xui calls should not leak into bot handlers. Do not expose a panel directly to Telegram users.

## Environment and keys

Generate independent random values for:

- `ADMIN_TOKEN`: at least 32 random bytes
- `INTERNAL_HMAC_KEY`: at least 32 random bytes, shared with tester
- `PAYLOAD_ENCRYPTION_KEY`: URL-safe base64 encoding of exactly 32 random bytes, shared with tester
- database and Redis credentials

S3 variables are optional but all must be supplied together. Arvan's endpoint is configured explicitly. Bucket lifecycle expiration is mandatory because application deletion is best-effort.

## Updates

1. Back up PostgreSQL and `.env`.
2. Pull the reviewed release.
3. Run `docker compose build --pull`.
4. Run database migrations before starting application containers.
5. Run `docker compose up -d --remove-orphans`.
6. Check readiness and Prometheus counters, then retain the previous image until the observation window ends.

Compose health checks prevent dependent services from starting against unhealthy databases. Install scripts are idempotent and support Ubuntu 22.04 and 24.04.

## SOCKS and S3

Proxy passwords belong in encrypted main-server records or tester environment secrets, never logs. The tester orders healthy proxies by explicit priority, latency and success rate, failing over immediately. If every proxy fails, Telegram-dependent tester work fails safely.

For S3, grant only list/get/delete on `pending/*` and put on `results/*` to the tester; use the inverse policy for main where possible. Enable server-side encryption in addition to application AES-GCM encryption.
