# SubIO VPN Bot — راهنمای نصب سرور اصلی (Main Server)

این سرور **خارج از ایران** deploy می‌شود و شامل ربات تلگرام، API، پنل ادمین، PostgreSQL، Redis و Worker است.

## پیش‌نیازها

- Ubuntu 22.04 یا 24.04 (64-bit)
- حداقل 2GB RAM و 20GB دیسک
- دامنه با TLS (Caddy/Nginx) برای API عمومی
- توکن ربات از [@BotFather](https://t.me/BotFather)
- سرور Tester ایران از قبل نصب و در دسترس

## نصب یک‌مرحله‌ای

```bash
git clone <repo-url> subio
cd subio/main-server
sudo ./install.sh
```

اسکریپت:
1. Docker و Compose را نصب می‌کند
2. در اولین اجرا `.env` با secretهای تصادفی می‌سازد
3. پس از تکمیل `.env`، imageها را build و سرویس‌ها را start می‌کند

## تنظیم `.env`

| متغیر | توضیح |
|--------|--------|
| `BOT_TOKEN` | توکن ربات تلگرام |
| `ADMIN_TOKEN` | توکن Bearer پنل ادمین (حداقل 32 کاراکتر) |
| `TESTER_BASE_URL` | آدرس HTTPS سرور ایران |
| `PUBLIC_BASE_URL` | آدرس عمومی API (برای لینک ساب) |
| `INTERNAL_HMAC_KEY` | کلید HMAC مشترک با Tester |
| `PAYLOAD_ENCRYPTION_KEY` | کلید AES-256 (base64url 32 بایت) مشترک |
| `ARVAN_S3_*` | اختیاری — برای Fallback ارتباط |
| `TELETHON_*` | برای اسکن کانال/گروه |

تولید کلید رمزنگاری:

```bash
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

## سرویس‌ها

| سرویس | نقش |
|--------|-----|
| `api` | FastAPI + Admin Panel |
| `bot` | ربات aiogram |
| `worker` | ARQ: probe، تست کانفیگ، SOCKS، cleanup |
| `monitor` | Telethon scanner (profile: monitoring) |
| `postgres` / `redis` | داده و cache |

```bash
docker compose ps
docker compose logs -f api bot worker
docker compose --profile monitoring up -d monitor
```

## پنل ادمین

- آدرس: `https://your-domain/admin`
- Header: `Authorization: Bearer <ADMIN_TOKEN>`

بخش‌ها: ارتباط Direct/S3، SOCKS، پنل‌های 3x-ui، ایموجی/رنگ، پیام timeout، broadcast.

## افزودن پنل 3x-ui

```bash
curl -X POST https://your-domain/admin/panels \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"DE-Frankfurt","base_url":"https://panel.example.com","username":"admin","password":"secret"}'
```

## ارتباط مقاوم

- **Primary:** HTTP مستقیم Main → Tester (HMAC + AES-GCM)
- **Fallback:** Arvan S3 (`pending/` → `results/`)
- Health Probe هر 60 ثانیه؛ 3 شکست → S3؛ 2 موفقیت → Direct
- Timeout سخت **10 ثانیه** برای عملیات Tester

## به‌روزرسانی

```bash
sudo cp .env .env.bak
git pull
sudo ./install.sh
```

## عیب‌یابی

```bash
docker compose logs api | tail -100
curl -s http://127.0.0.1:8000/health/ready
curl -s http://127.0.0.1:8000/admin/communication -H "Authorization: Bearer $ADMIN_TOKEN"
```
