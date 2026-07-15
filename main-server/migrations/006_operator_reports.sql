-- Operator-aware public feed: config codes/country, per-operator report
-- thresholds + exclusions, per-user public feed carrier, and a pipeline
-- event log for admin live monitoring.

-- 1. Stable, short, human-reportable config identifier + resolved country.
-- config_code holds only the numeric suffix (e.g. "454"), allocated from a
-- global sequence so uniqueness is guaranteed by construction. The visible
-- label — e.g. "🇮🇹 @Config_SubBOT #IT454" — is composed at render time from
-- country_code + config_code (see format_config_name), because the numeric
-- id must stay stable even if a config's resolved country is corrected
-- later by a re-test.
CREATE SEQUENCE IF NOT EXISTS vpn_config_code_seq START 100;

ALTER TABLE vpn_configs
  ADD COLUMN IF NOT EXISTS config_code VARCHAR(16) UNIQUE,
  ADD COLUMN IF NOT EXISTS country_code VARCHAR(8),
  ADD COLUMN IF NOT EXISTS is_globally_blocked BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS vpn_configs_config_code_idx ON vpn_configs(config_code);

-- Backfill config_code for pre-existing rows using the same sequence so
-- historical rows and newly-tested rows never collide.
UPDATE vpn_configs
SET config_code = nextval('vpn_config_code_seq')::text
WHERE config_code IS NULL;

-- Auto-assign config_code for every future insert (monitor.py ingestion,
-- manual test submissions, etc.) so application code never needs to
-- remember to allocate one — the "report a problem" flow can rely on every
-- row having a code from the moment it is created.
CREATE OR REPLACE FUNCTION assign_config_code() RETURNS trigger AS $$
BEGIN
  IF NEW.config_code IS NULL THEN
    NEW.config_code := nextval('vpn_config_code_seq')::text;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_assign_config_code ON vpn_configs;
CREATE TRIGGER trg_assign_config_code
  BEFORE INSERT ON vpn_configs
  FOR EACH ROW
  EXECUTE FUNCTION assign_config_code();

-- 2. Users carry their selected mobile operator explicitly (distinct from
-- users.mobile_operator, which is legacy/free-text set by AI report
-- inference). operator_code is the canonical key from the carrier-select
-- keyboard ("mci", "irancell", "rightel", "mtn_irancell", ..., "other:<name>").
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS operator_code VARCHAR(64),
  ADD COLUMN IF NOT EXISTS operator_label TEXT,
  ADD COLUMN IF NOT EXISTS operator_selected_at TIMESTAMPTZ;

-- 3. Per-operator config exclusions. A config is excluded for an operator
-- once its report count for that operator crosses the per-operator
-- threshold (default 5). Excluded rows are never served to users whose
-- operator_code matches, but the config can remain enabled/healthy on the
-- VPS and served to every other operator.
CREATE TABLE IF NOT EXISTS config_operator_exclusions (
  config_id UUID NOT NULL REFERENCES vpn_configs(id) ON DELETE CASCADE,
  operator_code VARCHAR(64) NOT NULL,
  report_count INTEGER NOT NULL DEFAULT 0,
  excluded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (config_id, operator_code)
);

-- 4. Structured problem reports linked by config_code (so AI parsing only
-- needs to resolve a short user-typed token, not a UUID) plus resolved
-- operator_code. One row per (reporter, config, operator) so a single user
-- reporting the same config repeatedly does not inflate the count; only
-- distinct users increase the vote toward a demotion threshold.
CREATE TABLE IF NOT EXISTS config_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  config_id UUID NOT NULL REFERENCES vpn_configs(id) ON DELETE CASCADE,
  reporter_user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  operator_code VARCHAR(64) NOT NULL,
  detail TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(config_id, reporter_user_id, operator_code)
);

CREATE INDEX IF NOT EXISTS config_reports_config_idx ON config_reports(config_id);

-- 5. Per-user public feed now records which config_ids were most recently
-- delivered, so an auto-replacement can target that specific user's feed
-- without waiting for the next scheduled sync, and records the owner's
-- operator at feed-build time for operator-aware filtering.
ALTER TABLE public_feeds
  ADD COLUMN IF NOT EXISTS operator_code VARCHAR(64),
  ADD COLUMN IF NOT EXISTS last_config_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
  ADD COLUMN IF NOT EXISTS excluded_config_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[];

-- 6. Live pipeline event log for the admin panel: one row per lifecycle
-- step of a config (ingest -> country lookup -> naming -> tested ->
-- promoted/demoted -> served). Kept lightweight (no huge payloads) and
-- pruned by the worker's cleanup job like test_jobs/comm_switch_logs.
CREATE TABLE IF NOT EXISTS pipeline_events (
  id BIGSERIAL PRIMARY KEY,
  config_id UUID REFERENCES vpn_configs(id) ON DELETE SET NULL,
  config_code VARCHAR(16),
  stage VARCHAR(32) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'info',
  message TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pipeline_events_created_idx ON pipeline_events(created_at DESC);
CREATE INDEX IF NOT EXISTS pipeline_events_config_idx ON pipeline_events(config_id);

INSERT INTO ui_assets(key, language, value, type, description) VALUES
  ('emoji_qrcode', 'fa', '🔗', 'emoji', 'دریافت QR کد'),
  ('emoji_change_link', 'fa', '♻️', 'emoji', 'تغییر لینک ساب'),
  ('emoji_operator', 'fa', '📶', 'emoji', 'انتخاب اپراتور'),
  ('emoji_star', 'fa', '⭐', 'emoji', 'کانفیگ برتر')
ON CONFLICT (key, language) DO NOTHING;

INSERT INTO system_messages(key, value, description) VALUES
  ('config_report_not_found', 'کد کانفیگ پیدا نشد. کد داخل نام کانفیگ، بعد از # نوشته شده است؛ مثلاً #IT454.', 'کد کانفیگ در گزارش پیدا نشد'),
  ('operator_prompt', 'برای دریافت کانفیگ‌های مناسب اپراتور شما، لطفاً اپراتور موبایل خودتان را انتخاب کنید.', 'درخواست انتخاب اپراتور')
ON CONFLICT (key) DO NOTHING;
