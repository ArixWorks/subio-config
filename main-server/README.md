# Main server

The system-of-record deployment: Telegram bot, control/subscription API, resilient tester client and background maintenance.

## Requirements

- Ubuntu 22.04/24.04, 2 CPU, 4 GB RAM, 25 GB disk
- DNS and TLS reverse proxy for port 8000
- Bot token and optional Telethon application credentials
- A separately deployed Iran tester

## Install

```bash
cp .env.example .env
chmod 600 .env
# edit and replace every CHANGE_ME
sudo ./install.sh
```

Only loopback port 8000 is published. Proxy `/` through Caddy or nginx with TLS. Restrict `/admin` to an operations network in addition to bearer authentication.

## Processes

- `api`: API, subscriptions, admin resources, tester dispatch, metrics
- `bot`: aiogram menus and user report/config flows
- `worker`: ARQ cleanup and communication probes
- `postgres`, `redis`: durable state and cache/rate limits

Health: `/health/live`, `/health/ready`; metrics: `/metrics`; OpenAPI: `/docs`.

The initial SQL runs only for a new PostgreSQL volume. Existing installations must apply reviewed migrations explicitly. Back up the database before updates.

## Operations

```bash
docker compose logs -f --since=10m api bot worker
docker compose exec postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup.sql
docker compose pull && docker compose build --pull
docker compose up -d --remove-orphans
```

Never paste `.env`, subscription URLs, proxy credentials or encrypted payloads into issue trackers.
