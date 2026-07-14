# SubIO VPN Bot v2.1

پلتفرم مقاوم تلگرام برای توزیع کانفیگ VPN با تست واقعی در ایران.

## معماری

```
[Telegram Users] → [Main Server: Bot + API + Admin]
                         ↕ Direct (HMAC) / Arvan S3 Fallback
                   [Iran Tester: xray-core + Multi-SOCKS]
```

## Deployments

| پوشه | محل deploy | نقش |
|------|------------|-----|
| [`main-server/`](main-server/README.fa.md) | خارج ایران | Bot, API, Admin, DB, Worker, Telethon |
| [`iran-tester/`](iran-tester/README.fa.md) | داخل ایران | تست xray، SOCKS، S3 poll |

## شروع سریع

```bash
# 1) Tester (Iran VPS)
cd iran-tester && sudo ./install.sh

# 2) Main (Foreign VPS)
cd main-server && sudo ./install.sh
```

## مستندات

- [راهنمای نصب کامل فارسی (VPS خارج + ایران)](docs/INSTALL.fa.md)
- [Architecture (EN)](docs/architecture.md)
- [Operations (EN)](docs/operations.md)
- [Main Server (FA)](main-server/README.fa.md)
- [Iran Tester (FA)](iran-tester/README.fa.md)

## لینک ساب کاربران

لینک ساب باید به **دامنه روی VPS ایران** اشاره کند (مثلاً `https://config.ir/sub/<TOKEN>`).  
سرور خارج لیست کانفیگ‌های سالم را هر چند دقیقه به ایران sync می‌کند تا حتی در قطع ارتباط ایران↔خارج، آپدیت ساب از داخل ایران کار کند.

## ویژگی‌های v2.1

- ارتباط Dual-Channel (Direct + Arvan S3) با Health Probe و Auto Switch
- Multi-SOCKS Failover برای Tester
- Timeout 10 ثانیه + پیام کاربرپسند
- اسکن Telethon + تست multi-site + Health Score
- py3xui: کانفیگ اختصاصی، حجم روزانه، referral، smart switch
- پنل ادمین وب (Tailwind)
- مدیریت Custom Emoji و دکمه‌های رنگی از DB
