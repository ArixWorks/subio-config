# SubIO Iran Tester — راهنمای نصب سرور تست (ایران)

سرور سبک داخل ایران برای تست واقعی کانفیگ با **xray-core**، Multi-SOCKS و Fallback S3.

## پیش‌نیازها

- Ubuntu 22.04/24.04
- حداقل 1GB RAM
- دسترسی outbound برای تست سایت‌ها
- لیست SOCKS5 برای دسترسی به Telegram API (در صورت نیاز)

## نصب

```bash
cd iran-tester
sudo ./install.sh
```

## `.env` مهم

| متغیر | توضیح |
|--------|--------|
| `INTERNAL_HMAC_KEY` | **همان** مقدار main-server |
| `PAYLOAD_ENCRYPTION_KEY` | **همان** مقدار main-server |
| `PROXY_URIS` | `socks5://user:pass@host:port,...` |
| `ARVAN_S3_*` | برای حالت Fallback (همان bucket main) |
| `MAX_OPERATION_SECONDS` | حداکثر 10 |

## امنیت شبکه

```bash
ufw allow from MAIN_SERVER_IP to any port 8080 proto tcp
ufw enable
```

## تست‌های انجام‌شده روی هر کانفیگ

1. Handshake xray-core
2. Ping / latency
3. دسترسی: Instagram, YouTube, Cloudflare, Telegram
4. Speed test کوچک (Cloudflare)
5. Health Score 0–100

## API

- `GET /health/ready` — آمادگی
- `POST /v1/tests` — تست مستقیم (HMAC)
- `GET /v1/socks/health` — وضعیت SOCKS
- `GET /metrics` — Prometheus

## Multi-SOCKS

پروکسی‌ها از `.env` یا sync از main-server خوانده می‌شوند. Failover خودکار بر اساس success rate و latency.

## عیب‌یابی

```bash
docker compose logs tester
curl -s http://127.0.0.1:8080/health/ready
curl -s -X POST http://127.0.0.1:8080/v1/socks/check
```
