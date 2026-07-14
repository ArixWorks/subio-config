# اجرای سریع SubIO بعد از تغییرات (AI + Retest)

ترتیب پیشنهادی: **اول ایران، بعد خارج**.

هر دو سرور: Ubuntu 22.04/24.04 + دسترسی root.

---

## ۱) سرور ایران (Tester + لینک ساب)

```bash
cd /opt/subio/iran-tester   # یا مسیر پروژه شما
chmod +x install.sh

# بار اول: .env ساخته می‌شود و اسکریپت می‌ایستد
sudo ./install.sh

# ویرایش کلیدهای مشترک با خارج
nano .env
# INTERNAL_HMAC_KEY=...
# PAYLOAD_ENCRYPTION_KEY=...
# ARVAN_S3_* و SOCKS در صورت نیاز

# اجرای نهایی (با دامنه و قفل IP خارج)
sudo ./install.sh --yes \
  --main-ip=IP_سرور_خارج \
  --with-caddy=tester.subio.vip,subio.vip
```

دستورهای بعدی:

```bash
subio-iran ps
subio-iran logs tester
subio-iran pull    # آپدیت کد + rebuild
```

---

## ۲) سرور خارج (Bot + API + Worker + DB)

```bash
cd /opt/subio/main-server
chmod +x install.sh

sudo ./install.sh          # بار اول → ساخت .env
nano .env                  # BOT_TOKEN ، HMAC مشترک ، PUBLIC_BASE_URL=https://subio.vip
                           # TESTER_BASE_URL=https://tester.subio.vip
                           # VERCEL_AI_GATEWAY_API_KEY=...

sudo ./install.sh --yes --with-caddy=api.subio.vip
# مانیتور Telethon اگر TELETHON_SESSION پر باشد خودکار بالا می‌آید
```

دستورهای بعدی:

```bash
subio-main ps
subio-main logs api bot worker
subio-main restart worker
subio-main pull
```

اگر مانیتور را جدا خواستید:

```bash
cd /opt/subio/main-server
docker compose --profile monitoring up -d
```

---

## ۳) چک سلامت

| کجا | دستور |
|-----|--------|
| خارج | `curl -s https://api.subio.vip/health/ready` |
| ایران | `curl -s https://tester.subio.vip/health/ready` |
| ساب کاربر | `curl -sI https://subio.vip/sub/<TOKEN>` |

پنل ادمین: `https://api.subio.vip/admin` (همان `ADMIN_TOKEN`)

---

## ۴) آپدیت بعد از `git pull`

روی **هر دو** سرور:

```bash
cd /opt/subio/main-server   # یا iran-tester
git pull
sudo ./install.sh --yes --skip-build=false
# یا ساده‌تر:
sudo ./install.sh --yes     # rebuild کامل
```

اسکریپت Main، migrationهای جدید (مثل `004_nullable_report_config.sql`) را روی DB موجود هم اعمال می‌کند.

---

## نکات دامنه شما

| دامنه | به | سرویس |
|--------|-----|--------|
| `subio.vip` | IP ایران | ساب کاربران `/sub/` |
| `tester.subio.vip` | IP ایران | API تست Main↔Iran |
| `api.subio.vip` | IP خارج | ربات‌API / ادمین |

`PUBLIC_BASE_URL` در main باید `https://subio.vip` باشد (ایران)، نه api.

جزئیات بیشتر: [INSTALL.fa.md](INSTALL.fa.md) · [AI_FEATURES.fa.md](AI_FEATURES.fa.md)
