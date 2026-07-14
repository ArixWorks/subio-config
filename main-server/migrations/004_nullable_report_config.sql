-- Allow free-text outage reports without a specific config UUID (AI-4).
ALTER TABLE user_reports ALTER COLUMN config_id DROP NOT NULL;
