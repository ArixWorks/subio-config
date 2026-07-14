# راهنمای کامل نصب و پیکربندی SubIO VPN Bot روی VPS

نسخه: ۲.۱ — جولای ۲۰۲۶

این سند از صفر تا استقرار تولید، نصب روی **دو سرور جدا** را پوشش می‌دهد:
- `main-server/` روی **VPS خارج از ایران**
- `iran-tester/` روی **VPS داخل ایران** (تستر + تحویل لینک ساب به کاربر)

---

## ۱) معماری و نکته حیاتی لینک ساب

```
کاربر داخل ایران
      │
      │  آپدیت ساب در v2rayN / Streisand / ...
      ▼
https://config.ir/sub/<TOKEN>     ←── باید به VPS ایران اشاره کند
      │
      │  از کش محلی روی ایران (آخرین همگام‌سازی)
      ▼
لیست کانفیگ‌های سالم (plaintext)
```

| سرویس | محل | نقش کاربر |
|--------|------|-----------|
| ربات تلگرام + پنل ادمین + PostgreSQL | **خارج** | ساخت ساب، مدیریت، اسکن |
| تست xray + **تحویل `/sub/`** | **ایران** | رسیدن کاربر به کانفیگ حتی اگر ارتباط ایران↔خارج قطع باشد |

### وضعیت کد

- قبلاً `/sub/` فقط روی سرور خارج بود.
- الان:
  - ایران: `GET /sub/{token}` از کش محلی
  - خارج: هر ۵ دقیقه ساب‌ها را به ایران push می‌کند (Direct؛ اگر قطع بود از Arvan S3 با prefix `subs/`)
  - ربات لینک را با `PUBLIC_BASE_URL` می‌سازد → **این را روی دامنه ایران بگذار** (مثل `https://config.ir`)

اگر اینترنت ایران↔خارج قطع شود:
- ربات ممکن است موقتاً کند/قطع شود (روی خارج است)
- **آپدیت ساب کاربر داخل ایران** از کش ایران ادامه می‌دهد (تا وقتی آخرین sync موفق بوده باشد)

---

## ۲) پیش‌نیازها

### سخت‌افزار پیشنهادی

| سرور | OS | RAM | دیسک |
|------|-----|-----|------|
| خارج (Main) | Ubuntu 22.04 یا 24.04 | ≥ ۲GB | ≥ ۲۰GB |
| ایران (Tester + Sub) | Ubuntu 22.04 یا 24.04 | ≥ ۱GB | ≥ ۱۰GB |

### چیزهایی که قبل از نصب آماده کنید

1. توکن ربات از [@BotFather](https://t.me/BotFather) → `BOT_TOKEN`
2. آیدی عددی ادمین (مثلاً از `@userinfobot`) → `ADMIN_TELEGRAM_IDS`
3. سه دامنه (یا ساب‌دامنه):

| دامنه نمونه | DNS به | کاربرد |
|-------------|--------|--------|
| `api.yourdomain.com` | IP خارج | API ادمین + health |
| `tester.yourdomain.com` | IP ایران | ارتباط داخلی Main↔Tester (HTTPS) |
| `config.ir` یا `sub.yourdomain.ir` | **IP ایران** | لینک ساب عمومی کاربران |

4. دو کلید **مشترک** بین دو سرور (بعداً در هر دو `.env` یکی باشند):

```bash
# HMAC — حداقل ۳۲ کاراکتر
openssl rand -base64 48 | tr -d '/+=' | head -c 48
echo

# AES key — دقیقاً ۳۲ بایت به صورت base64url
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

ذخیره کنید:
- `INTERNAL_HMAC_KEY=...`
- `PAYLOAD_ENCRYPTION_KEY=...`

5. کد پروژه روی هر دو VPS (git clone یا scp)

```bash
# مثال
git clone <REPO_URL> /opt/subio
cd /opt/subio
```

---

## ۳) نصب مرحله‌به‌مرحله — اول ایران

> ترتیب مهم است: اول ایران، بعد خارج.

### ۳.۱ اجرای نصب‌کننده

```bash
cd /opt/subio/iran-tester
chmod +x install.sh
sudo ./install.sh
# .env را پر کنید، سپس:
sudo ./install.sh --yes --main-ip=IP_خارج --with-caddy=tester.subio.vip,subio.vip
```

راهنمای یک‌صفحه‌ای اجرا: [`docs/RUN.fa.md`](RUN.fa.md)

بار اول `.env` ساخته می‌شود و اسکریپت متوقف می‌شود تا مقادیر را پر کنید.

### ۳.۲ ویرایش `.env` ایران

```bash
sudo nano /opt/subio/iran-tester/.env
```

نمونه:

```env
ENVIRONMENT=production
LOG_LEVEL=INFO

INTERNAL_HMAC_KEY=اینجا_همان_HMAC
PAYLOAD_ENCRYPTION_KEY=اینجا_همان_PAYLOAD

MAX_OPERATION_SECONDS=10
XRAY_BINARY=/usr/local/bin/xray
XRAY_TEST_URL=https://cp.cloudflare.com/generate_204

# اختیاری — SOCKS برای دسترسی به تلگرام از ایران
SOCKS_PROXIES=socks5://user:pass@host:1080

# محل کش ساب‌ها روی دیسک
SUBSCRIPTION_STORE_DIR=/data/subs

# اختیاری — Arvan (برای قطع ارتباط مستقیم با خارج)
ARVAN_S3_ENDPOINT=
ARVAN_S3_REGION=ir-thr-at1
ARVAN_S3_BUCKET=
ARVAN_S3_ACCESS_KEY=
ARVAN_S3_SECRET_KEY=
S3_POLL_SECONDS=3
```

### ۳.۳ راه‌اندازی نهایی ایران

```bash
sudo ./install.sh
curl -s http://127.0.0.1:8080/health/live
curl -s http://127.0.0.1:8080/health/ready
docker compose ps
```

### ۳.۴ فایروال ایران

```bash
MAIN_IP=x.x.x.x   # IP سرور خارج
ufw allow OpenSSH
ufw allow from $MAIN_IP to any port 443 proto tcp
ufw enable
```

### ۳.۵ TLS با Caddy روی ایران (دو دامنه روی همین سرور)

```bash
sudo apt install -y caddy
sudo nano /etc/caddy/Caddyfile
```

```caddy
tester.yourdomain.com {
    reverse_proxy 127.0.0.1:8080
}

# لینک ساب کاربران — همین سرویس ایران
config.ir {
    reverse_proxy 127.0.0.1:8080
}
```

```bash
sudo systemctl reload caddy
curl -s https://tester.yourdomain.com/health/live
curl -s https://config.ir/health/live
```

DNS:
- `tester.yourdomain.com` → IP ایران
- `config.ir` → IP ایران

---

## ۴) نصب مرحله‌به‌مرحله — سرور خارج

### ۴.۱ اجرا

```bash
cd /opt/subio/main-server
chmod +x install.sh
sudo ./install.sh
```

### ۴.۲ ویرایش `.env` خارج

```bash
sudo nano /opt/subio/main-server/.env
```

```env
ENVIRONMENT=production
LOG_LEVEL=INFO

BOT_TOKEN=123456:ABC...
ADMIN_TELEGRAM_IDS=123456789
ADMIN_TOKEN=رمز_بلند_حداقل_۳۲_کاراکتر

POSTGRES_DB=subio
POSTGRES_USER=subio
POSTGRES_PASSWORD=پسورد_قوی_postgres
REDIS_PASSWORD=پسورد_قوی_redis

DATABASE_URL=postgresql+asyncpg://subio:پسورد_قوی_postgres@postgres:5432/subio
REDIS_URL=redis://:پسورد_قوی_redis@redis:6379/0

# HTTPS تستر ایران (برای تست و sync ساب)
TESTER_BASE_URL=https://tester.yourdomain.com

# لینک ساب که به کاربر داده می‌شود → دامنه ایران
PUBLIC_BASE_URL=https://config.ir

# کلیدهای مشترک با ایران (دقیقاً یکی)
INTERNAL_HMAC_KEY=اینجا_همان_HMAC
PAYLOAD_ENCRYPTION_KEY=اینجا_همان_PAYLOAD

TESTER_TIMEOUT_SECONDS=10
BREAKER_FAILURE_THRESHOLD=3
BREAKER_RECOVERY_SUCCESSES=2
BREAKER_RESET_SECONDS=45

# Telethon — بعداً برای اسکن کانال
TELETHON_API_ID=
TELETHON_API_HASH=
TELETHON_SESSION=
TELETHON_SOURCE_CHATS=

# اختیاری Arvan
ARVAN_S3_ENDPOINT=
ARVAN_S3_REGION=ir-thr-at1
ARVAN_S3_BUCKET=
ARVAN_S3_ACCESS_KEY=
ARVAN_S3_SECRET_KEY=

# Vercel AI Gateway (موارد ۱–۱۲) — جزئیات: docs/AI_FEATURES.fa.md
AI_ENABLED=true
VERCEL_AI_GATEWAY_API_KEY=
AI_GATEWAY_BASE_URL=https://ai-gateway.vercel.sh/v1
AI_MODEL_SOL=openai/gpt-5.6-sol
AI_MODEL_LUNA=openai/gpt-5.6-luna
RETEST_HEALTHY_INTERVAL_SECONDS=10
RETEST_HEALTHY_BATCH=8
RETEST_DEAD_INTERVAL_SECONDS=180
RETEST_DEAD_BATCH=5
RETEST_DEMOTE_ON_FIRST_FAIL=true
```

> اگر `install.sh` خودش پسورد ساخته، همان مقادیر را داخل `DATABASE_URL` و `REDIS_URL` هم یکسان کنید.
>
> بعد از آپدیت کد، migrationی `migrations/002_nullable_report_config.sql` را اعمال کنید و پکیج `openai` را روی main نصب کنید.
### ۴.۳ راه‌اندازی

```bash
sudo ./install.sh
curl -s http://127.0.0.1:8000/health/live
curl -s http://127.0.0.1:8000/health/ready
docker compose ps
```

### ۴.۴ Caddy خارج

```caddy
api.yourdomain.com {
    reverse_proxy 127.0.0.1:8000
}
```

```bash
ufw allow OpenSSH
ufw allow 80,443/tcp
ufw enable
sudo systemctl reload caddy
curl -s https://api.yourdomain.com/health/ready
```

---

## ۵) تست ارتباط و همگام‌سازی ساب

روی سرور خارج:

```bash
export ADMIN_TOKEN='مقدار_ADMIN_TOKEN'

# آیا از خارج به تستر ایران می‌رسد؟
curl -s https://tester.yourdomain.com/health/ready

# Probe ارتباط
curl -s -X POST https://api.yourdomain.com/admin/communication/probe \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# وضعیت breaker
curl -s https://api.yourdomain.com/admin/communication \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

Worker هر ~۵ دقیقه ساب‌های فعال را به ایران می‌فرستد. برای مشاهده لاگ:

```bash
cd /opt/subio/main-server
docker compose logs -f worker
```

روی ایران بررسی endpoint ساب (بعد از sync):

```bash
# بعد از اینکه ربات برای کاربر ساب ساخت و sync شد:
curl -s "https://config.ir/sub/<TOKEN_UUID>"
```

---

## ۶) پنل ادمین و تنظیمات اولیه

1. مرورگر: `https://api.yourdomain.com/admin`
2. Token = همان `ADMIN_TOKEN`
3. بخش **تنظیمات اسکنر**:
   - اگر `@VPNDecryptorBot` خراب است → خاموش کنید
   - NPV→v2ray محلی را در صورت نیاز روشن نگه دارید
   - فقط پروتکل‌های لازم را فعال کنید

### افزودن پنل 3x-ui (اختیاری)

```bash
curl -X POST https://api.yourdomain.com/admin/panels \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name":"DE-Frankfurt",
    "base_url":"https://panel.example.com",
    "username":"admin",
    "password":"SECRET"
  }'
```

### کانال اجباری (اختیاری)

ربات را در کانال Admin کنید، سپس:

```bash
cd /opt/subio/main-server
docker compose exec postgres psql -U subio -d subio -c "
INSERT INTO forced_channels(chat_id, title, username, invite_link)
VALUES (-100xxxxxxxxxx, 'SubIO', 'your_channel', 'https://t.me/your_channel');
"
```

---

## ۷) تست ربات

1. در تلگرام ربات را `/start` کنید.
2. «اشتراک من» → لینک باید شبیه باشد:

```text
https://config.ir/sub/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

3. این لینک را در کلاینت v2ray وارد کنید و Update بزنید.
4. روی ایران باید پاسخ متنی کانفیگ‌ها برگردد؛ نه از سرور خارج.

لاگ ربات:

```bash
docker compose logs -f bot
```

---

## ۸) اسکن کانال‌ها با Telethon (اختیاری)

1. از [my.telegram.org](https://my.telegram.org) بگیر: `api_id` / `api_hash`
2. ساخت String Session روی لپ‌تاپ:

```bash
pip install telethon
python3 - <<'PY'
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
api_id = 123456
api_hash = "xxxx"
with TelegramClient(StringSession(), api_id, api_hash) as c:
    print(c.session.save())
PY
```

3. در `.env` main پر کنید و restart:

```bash
docker compose up -d --force-recreate bot worker api
docker compose --profile monitoring up -d monitor
docker compose logs -f monitor
```

اکانت Telethon باید عضو کانال‌های منبع باشد. اگر Decrypt روشن است یک‌بار به `@VPNDecryptorBot` `/start` بزنید.

---

## ۹) Arvan S3 (پیشنهادی برای قطعی ایران↔خارج)

وقتی Direct قطع است:

| مسیر | کاربرد |
|------|--------|
| `pending/` | ارسال کانفیگ برای تست |
| `results/` | نتیجه تست |
| `subs/` | آپدیت کش ساب ایران |

در **هر دو** `.env` یک bucket و کلید یکسان بگذارید و Lifecycle ۳۰–۶۰ دقیقه روی bucket فعال کنید.

---

## ۱۰) چک‌لیست نهایی

| مورد | انتظار |
|------|--------|
| `https://api.yourdomain.com/health/ready` | OK |
| `https://tester.yourdomain.com/health/ready` | OK |
| `https://config.ir/health/live` | OK |
| `PUBLIC_BASE_URL=https://config.ir` | لینک ربات به ایران باشد |
| `POST /admin/communication/probe` | موفق / mode=direct |
| لینک ساب در کلاینت | آپدیت از ایران |
| قطع فرضی Main | ساب همچنان از ایران (کش) جواب بدهد |

---

## ۱۱) دستورات نگهداری

```bash
# وضعیت
docker compose ps
docker compose logs -f api bot worker

# به‌روزرسانی
cp .env .env.bak
git pull
sudo ./install.sh

# بکاپ DB (خارج)
docker compose exec postgres pg_dump -U subio subio > backup-$(date +%F).sql
```

---

## ۱۲) مشکلات رایج

| مشکل | راه‌حل |
|------|--------|
| ربات لینک با دامنه خارج می‌دهد | `PUBLIC_BASE_URL` را روی `https://config.ir` بگذارید و `bot` را recreate کنید |
| `/sub/` روی ایران 404 | هنوز sync نشده؛ لاگ `worker` و probe را ببینید؛ ۵–۱۰ دقیقه صبر کنید |
| Probe fail | کلیدهای HMAC/Payload یکسان نیستند یا دامنه tester در دسترس نیست |
| install.sh متوقف | هنوز `CHANGE_ME` در `.env` مانده |
| Tester not ready | `docker compose logs tester` — مشکل xray |
| Admin 401 | `ADMIN_TOKEN` اشتباه |

---

## ۱۳) مسیر مینیمال (سریع‌ترین استقرار)

1. ایران: `install.sh` + `config.ir` و `tester…` روی Caddy + فایروال فقط از IP خارج  
2. خارج: `BOT_TOKEN` + کلیدهای مشترک + `TESTER_BASE_URL` + `PUBLIC_BASE_URL=https://config.ir`  
3. ربات `/start` → لینک ساب ایران → وارد کلاینت  
4. Telethon / S3 / 3x-ui را بعداً اضافه کنید  

---

**فایل‌های مرتبط:**  
`main-server/README.fa.md` · `iran-tester/README.fa.md` · `docs/architecture.md`
