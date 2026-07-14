-- Premium Persian bot: permanent public feeds and panel location metadata.

CREATE TABLE IF NOT EXISTS public_feeds (
  user_id BIGINT PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
  token UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE panels
  ADD COLUMN IF NOT EXISTS country_code VARCHAR(8),
  ADD COLUMN IF NOT EXISTS country_name_fa VARCHAR(64),
  ADD COLUMN IF NOT EXISTS flag_emoji_key VARCHAR(64),
  ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 100;

WITH panel_locations AS (
  SELECT
    id,
    COALESCE(
      UPPER(substring(name FROM '^([A-Za-z]{2})(?:[-_ ]|$)')),
      UPPER(substring(replace(id::text, '-', '') FROM 1 FOR 8))
    ) AS inferred_code
  FROM panels
)
UPDATE panels p
SET
  country_code=COALESCE(p.country_code, locations.inferred_code),
  country_name_fa=COALESCE(p.country_name_fa, p.name),
  flag_emoji_key=COALESCE(
    p.flag_emoji_key,
    CASE
      WHEN length(locations.inferred_code)=2
      THEN 'flag_' || lower(locations.inferred_code)
      ELSE 'location'
    END
  )
FROM panel_locations locations
WHERE p.id=locations.id
  AND (p.country_code IS NULL OR p.country_name_fa IS NULL OR p.flag_emoji_key IS NULL);

ALTER TABLE ui_assets
  ADD COLUMN IF NOT EXISTS fallback_value TEXT;

ALTER TABLE subscriptions
  ADD COLUMN IF NOT EXISTS service_day DATE,
  ADD COLUMN IF NOT EXISTS location_code VARCHAR(8);

ALTER TABLE panel_clients
  ADD COLUMN IF NOT EXISTS usage_offset_bytes BIGINT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS panel_cleanup_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  panel_id UUID NOT NULL REFERENCES panels(id) ON DELETE RESTRICT,
  inbound_id BIGINT NOT NULL,
  client_uuid UUID NOT NULL,
  client_email TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(panel_id, client_uuid)
);

UPDATE subscriptions stranded
SET
  location_code='DUPLIC8',
  is_active=FALSE
WHERE stranded.service_day IS NULL
  AND stranded.location_code IS DISTINCT FROM 'DUPLIC8'
  AND EXISTS (
    SELECT 1
    FROM subscriptions allocated
    WHERE allocated.user_id=stranded.user_id
      AND allocated.id<>stranded.id
      AND allocated.service_day IS NOT NULL
  );

UPDATE subscriptions s
SET
  service_day=CASE
    WHEN s.is_active AND s.expires_at>now()
    THEN (now() AT TIME ZONE 'Asia/Tehran')::date
    ELSE (s.created_at AT TIME ZONE 'Asia/Tehran')::date
  END,
  location_code=COALESCE(
    s.location_code,
    (
      SELECT pc.location_code
      FROM panel_clients pc
      WHERE pc.subscription_id=s.id
      ORDER BY pc.is_active DESC, pc.created_at DESC
      LIMIT 1
    ),
    'LEGACY'
  )
WHERE s.service_day IS NULL
  AND s.location_code IS DISTINCT FROM 'DUPLIC8';

WITH duplicate_days AS (
  SELECT
    id,
    row_number() OVER (
      PARTITION BY user_id, service_day
      ORDER BY is_active DESC, expires_at DESC, created_at DESC
    ) AS position
  FROM subscriptions
  WHERE service_day IS NOT NULL
)
UPDATE subscriptions s
SET
  service_day=NULL,
  location_code='DUPLIC8',
  is_active=FALSE
FROM duplicate_days duplicates
WHERE s.id=duplicates.id AND duplicates.position>1;

DROP INDEX IF EXISTS subscriptions_daily_location_idx;

CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_daily_user_idx
  ON subscriptions(user_id, service_day)
  WHERE service_day IS NOT NULL;

INSERT INTO ui_assets(key, language, value, type, description) VALUES
  ('emoji_home', 'fa', '🏠', 'emoji', 'منوی اصلی'),
  ('emoji_help', 'fa', '🧭', 'emoji', 'راهنما'),
  ('emoji_account', 'fa', '👤', 'emoji', 'حساب کاربری'),
  ('emoji_location', 'fa', '🌍', 'emoji', 'انتخاب کشور'),
  ('emoji_refresh', 'fa', '🔄', 'emoji', 'به‌روزرسانی'),
  ('emoji_back', 'fa', '↩️', 'emoji', 'بازگشت'),
  ('emoji_copy', 'fa', '🔗', 'emoji', 'لینک اشتراک')
ON CONFLICT (key, language) DO NOTHING;

INSERT INTO system_messages(key, value, description) VALUES
  (
    'bot_welcome',
    'به SubIO خوش آمدید؛ دسترسی سریع، مطمئن و همیشه به‌روز به اینترنت آزاد.',
    'متن خوش‌آمدگویی بات'
  ),
  (
    'public_help',
    'ساب عمومی از کانفیگ‌های سالمی ساخته می‌شود که در سطح اینترنت اسکن و داخل ایران تست شده‌اند. این بخش محدودیت حجم و تاریخ انقضا ندارد و محتوای لینک آن خودکار به‌روز می‌شود.',
    'توضیح ساب عمومی'
  ),
  (
    'private_help',
    'اشتراک اختصاصی مستقیماً روی یکی از سرورهای SubIO ساخته می‌شود. کشور را انتخاب می‌کنید، حجم روزانه دریافت می‌کنید و اشتراک رأس ساعت ۰۰:۰۰ تهران منقضی می‌شود.',
    'توضیح اشتراک اختصاصی'
  )
ON CONFLICT (key) DO NOTHING;
