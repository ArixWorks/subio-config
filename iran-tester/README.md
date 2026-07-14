# Iran tester

Hardened stateless service that validates VPN configurations from inside Iran. It has no Telegram bot or panel credentials.

## Install

Provision Ubuntu 22.04/24.04, place TLS and an IP allow-list in front of loopback port 8080, then:

```bash
cp .env.example .env
chmod 600 .env
# set the same HMAC and payload keys as main-server
sudo ./install.sh
```

`SOCKS_PROXIES` is a comma-separated ordered list of `socks5://user:password@host:port` URIs. URI credentials are never logged. Health checks continuously update in-memory latency, success rate and failure counts; production synchronization from the main database should use an authenticated control endpoint.

The container is read-only, drops all Linux capabilities, limits memory/PIDs/CPU and uses `/tmp` only for per-job xray configuration. Every job is killed and cleaned up before the absolute 10-second deadline.

Endpoints: `/health/live`, `/health/ready`, `/v1/tests`, `/v1/socks/health`, `/metrics`.

For S3 fallback, configure all Arvan variables and a 30–60 minute bucket lifecycle. Application payload encryption is mandatory even when bucket-side encryption is enabled.
