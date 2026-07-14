# نقشه راه و وضعیت پیاده‌سازی قابلیت‌های هوش مصنوعی SubIO + بازآزمایی کانفیگ

مدل اصلی: `openai/gpt-5.6-sol`  
مدل سریع: `openai/gpt-5.6-luna`  
Gateway: Vercel AI Gateway — `https://ai-gateway.vercel.sh/v1`

---

## اصل ترافیک ایران (غیرقابل مذاکره)

| نوع تست | چه وقتی | مصرف دانلود تقریبی |
|---------|---------|---------------------|
| `cheap` | بازآزمایی دوره‌ای سالم و خراب | خیلی کم (handshake + 1–۲ چک سبک + حداکثر ۱۶KiB) |
| `full` | کانفیگ جدید، یا ارتقای خراب→سالم | متوسط (multi-site + سرعت ~۱MiB) |

**قانون:** آپلود ایران تقریباً رایگان است؛ دانلود را کم نگه می‌داریم. منطق صف و AI روی سرور خارج (۸GB RAM / ۶ CPU) اجرا می‌شود.

### چرخه سالم ⇄ خراب (پیاده‌سازی‌شده)

```
جدید → full test → سالم (score≥50, enabled)
سالم → هر ۱۰ثانیه (batch نوبتی، concurrency=3) cheap test
  ├─ OK → ماندن در سالم + به‌روزرسانی latency/score
  └─ FAIL → فوری is_enabled=FALSE (حذف از لیست/مخزن سالم)
خراب → هر ~۳ دقیقه cheap
  ├─ OK → یک full test تأیید → برگشت به سالم
  └─ FAIL → ماندن در خراب
```

Worker: `retest_healthy` / `retest_dead` در `main-server/app/worker.py`  
Tester: `mode` در `iran-tester/app/xray.py` و `/v1/tests`

---

## وضعیت ۱۲ قابلیت AI

| # | قابلیت | فایل | وضعیت |
|---|--------|------|--------|
| 1 | دسته‌بندی پیام/فایل | `app/ai/classify.py` → scanner | ✅ |
| 2 | استخراج از متن درهم | `app/ai/extract.py` | ✅ |
| 3 | نام‌گذاری کانفیگ | `app/ai/naming.py` → config_tester | ✅ |
| 4 | اپراتور از گزارش فارسی | `app/ai/reports.py` → bot free-text | ✅ |
| 5 | دستیار ادمین | `admin_assistant.py` + `/admin/ai/chat` + `/ask` | ✅ |
| 6 | خلاصه روزانه | `daily_digest.py` + cron 07:30 | ✅ |
| 7 | بهبود برودکست | `broadcast_polish.py` قبل از ارسال | ✅ |
| 8 | کمک خطای کاربر | `user_help.py` در bot | ✅ |
| 9 | triage لاگ | `log_triage.py` + cron | ✅ |
| 10 | فیلتر امنیتی | `security_filter.py` در monitor | ✅ |
| 11 | چندزبانه | `i18n.py` + Redis cache | ✅ |
| 12 | پیشنهاد اسکنر | `scanner_advisor.py` + اعلان ادمین | ✅ |

Client مشترک: `app/ai/gateway.py`

---

## ENV لازم

```
AI_ENABLED=true
VERCEL_AI_GATEWAY_API_KEY=vck_...
AI_GATEWAY_BASE_URL=https://ai-gateway.vercel.sh/v1
AI_MODEL_SOL=openai/gpt-5.6-sol
AI_MODEL_LUNA=openai/gpt-5.6-luna
RETEST_HEALTHY_INTERVAL_SECONDS=10
RETEST_HEALTHY_BATCH=8
RETEST_DEAD_INTERVAL_SECONDS=180
RETEST_DEAD_BATCH=5
RETEST_DEMOTE_ON_FIRST_FAIL=true
```

Migration: `migrations/002_nullable_report_config.sql` (گزارش آزاد بدون UUID)

---

## نکات عملیاتی

1. بعد از deploy روی Main: `pip install -e .` تا `openai` نصب شود.
2. Migration `002` را روی Postgres اعمال کنید.
3. Worker را حتماً ری‌استارت کنید تا cronهای AI و retest فعال شوند.
4. کلید Gateway را فقط در `.env` نگه دارید؛ commit نکنید.
