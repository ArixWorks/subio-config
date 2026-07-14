-- SubIO v2.1 extensions

ALTER TABLE vpn_configs
  ADD COLUMN IF NOT EXISTS display_name TEXT,
  ADD COLUMN IF NOT EXISTS source_chat TEXT,
  ADD COLUMN IF NOT EXISTS transport_type VARCHAR(32),
  ADD COLUMN IF NOT EXISTS operator_scores JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS mobile_operator VARCHAR(32) DEFAULT 'unknown';

CREATE TABLE IF NOT EXISTS forced_channels (
  id BIGSERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL UNIQUE,
  username TEXT,
  title TEXT,
  invite_link TEXT,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS panel_clients (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  panel_id UUID NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
  subscription_id UUID NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
  inbound_id INTEGER NOT NULL,
  client_uuid UUID NOT NULL,
  client_email TEXT NOT NULL,
  sub_id TEXT,
  panel_client_id INTEGER,
  location_code VARCHAR(8) NOT NULL DEFAULT 'DE',
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(panel_id, client_email)
);

CREATE TABLE IF NOT EXISTS broadcasts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message TEXT NOT NULL,
  target VARCHAR(20) NOT NULL DEFAULT 'all',
  sent_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  created_by BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS system_messages (
  key VARCHAR(64) PRIMARY KEY,
  value TEXT NOT NULL,
  description TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO system_messages(key, value, description) VALUES
  ('tester_timeout', 'در حال حاضر به دلیل اختلال موقت در ارتباط تست‌کننده‌ها، امکان ساخت / به‌روزرسانی کانفیگ وجود ندارد. لطفاً چند دقیقه دیگر دوباره تلاش کنید.', 'پیام timeout تستر'),
  ('retry_cooldown_sec', '30', 'Cooldown دکمه تلاش مجدد')
ON CONFLICT (key) DO NOTHING;

INSERT INTO ui_assets(key, language, value, type, description) VALUES
  ('emoji_public', 'fa', '📡', 'emoji', 'بخش کانفیگ عمومی'),
  ('emoji_private', 'fa', '🔐', 'emoji', 'بخش اختصاصی'),
  ('emoji_success', 'fa', '✅', 'emoji', 'عملیات موفق'),
  ('emoji_error', 'fa', '⚠️', 'emoji', 'خطا'),
  ('emoji_premium', 'fa', '💎', 'emoji', 'پلن پولی'),
  ('emoji_referral', 'fa', '👥', 'emoji', 'دعوت دوستان'),
  ('emoji_loading', 'fa', '⏳', 'emoji', 'در حال پردازش'),
  ('emoji_report', 'fa', '🚨', 'emoji', 'گزارش خرابی'),
  ('btn_color_primary', 'fa', 'primary', 'color', 'دکمه اصلی'),
  ('btn_color_success', 'fa', 'success', 'color', 'دکمه موفقیت'),
  ('btn_color_danger', 'fa', 'danger', 'color', 'دکمه خطر')
ON CONFLICT (key, language) DO NOTHING;
