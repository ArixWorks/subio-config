CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
  telegram_id BIGINT PRIMARY KEY,
  username TEXT,
  language VARCHAR(5) NOT NULL DEFAULT 'fa',
  referred_by BIGINT REFERENCES users(telegram_id),
  referral_credit_bytes BIGINT NOT NULL DEFAULT 0 CHECK (referral_credit_bytes >= 0),
  is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE plans (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  duration_days INTEGER NOT NULL CHECK (duration_days > 0),
  volume_bytes BIGINT NOT NULL CHECK (volume_bytes > 0),
  price_minor BIGINT NOT NULL CHECK (price_minor >= 0),
  is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  token UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
  user_id BIGINT NOT NULL REFERENCES users(telegram_id),
  plan_id UUID REFERENCES plans(id),
  volume_limit_bytes BIGINT NOT NULL CHECK (volume_limit_bytes > 0),
  volume_used_bytes BIGINT NOT NULL DEFAULT 0 CHECK (volume_used_bytes >= 0),
  expires_at TIMESTAMPTZ NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX subscriptions_user_active_idx ON subscriptions(user_id, is_active);

CREATE TYPE config_scope AS ENUM ('public', 'private');
CREATE TABLE vpn_configs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope config_scope NOT NULL,
  subscription_id UUID REFERENCES subscriptions(id) ON DELETE CASCADE,
  protocol VARCHAR(20) NOT NULL,
  fingerprint CHAR(64) NOT NULL UNIQUE,
  uri_enc TEXT NOT NULL,
  score NUMERIC(6,2) NOT NULL DEFAULT 50 CHECK (score BETWEEN 0 AND 100),
  latency_ms INTEGER,
  success_rate NUMERIC(5,2) NOT NULL DEFAULT 100,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  tested_at TIMESTAMPTZ,
  CHECK ((scope = 'public' AND subscription_id IS NULL) OR (scope = 'private' AND subscription_id IS NOT NULL))
);
CREATE INDEX vpn_configs_distribution_idx ON vpn_configs(scope, is_enabled, score DESC);

CREATE TABLE panels (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL UNIQUE,
  base_url TEXT NOT NULL,
  username_enc TEXT NOT NULL,
  password_enc TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id BIGINT NOT NULL REFERENCES users(telegram_id),
  config_id UUID NOT NULL REFERENCES vpn_configs(id),
  category VARCHAR(32) NOT NULL,
  detail TEXT,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE test_jobs (
  id UUID PRIMARY KEY,
  config_id UUID REFERENCES vpn_configs(id) ON DELETE SET NULL,
  payload_enc TEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  transport VARCHAR(20),
  attempts INTEGER NOT NULL DEFAULT 0,
  error_code VARCHAR(64),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);
CREATE INDEX test_jobs_pending_idx ON test_jobs(status, created_at);

CREATE TABLE test_results (
  job_id UUID PRIMARY KEY REFERENCES test_jobs(id) ON DELETE CASCADE,
  reachable BOOLEAN NOT NULL,
  latency_ms INTEGER,
  download_mbps NUMERIC(10,2),
  checks JSONB NOT NULL DEFAULT '{}'::jsonb,
  health_score NUMERIC(6,2) NOT NULL CHECK (health_score BETWEEN 0 AND 100),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE socks_proxies (
  id BIGSERIAL PRIMARY KEY,
  name TEXT,
  host TEXT NOT NULL,
  port INTEGER NOT NULL CHECK (port BETWEEN 1 AND 65535),
  username TEXT,
  password_enc TEXT,
  protocol VARCHAR(10) NOT NULL DEFAULT 'socks5' CHECK (protocol IN ('socks4','socks5')),
  priority INTEGER NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  last_checked_at TIMESTAMPTZ,
  last_latency_ms INTEGER,
  success_rate NUMERIC(5,2) NOT NULL DEFAULT 100,
  fail_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(host, port, username)
);

CREATE TABLE system_comm_state (
  singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
  mode VARCHAR(20) NOT NULL DEFAULT 'direct' CHECK (mode IN ('direct','arvan_s3')),
  forced_mode VARCHAR(20) CHECK (forced_mode IN ('direct','arvan_s3')),
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  recovery_successes INTEGER NOT NULL DEFAULT 0,
  last_direct_success TIMESTAMPTZ,
  last_switch_at TIMESTAMPTZ,
  probe_interval_sec INTEGER NOT NULL DEFAULT 45 CHECK (probe_interval_sec BETWEEN 30 AND 60),
  fail_threshold INTEGER NOT NULL DEFAULT 3 CHECK (fail_threshold > 0)
);
INSERT INTO system_comm_state(singleton) VALUES(TRUE);

CREATE TABLE comm_switch_logs (
  id BIGSERIAL PRIMARY KEY,
  from_mode VARCHAR(20),
  to_mode VARCHAR(20) NOT NULL,
  reason TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ui_assets (
  key VARCHAR(64) NOT NULL,
  language VARCHAR(5) NOT NULL DEFAULT 'fa',
  value TEXT NOT NULL,
  type VARCHAR(20) NOT NULL DEFAULT 'emoji' CHECK (type IN ('emoji','custom_emoji','color','text')),
  description TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by BIGINT,
  PRIMARY KEY(key, language)
);
