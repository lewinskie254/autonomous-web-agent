-- Run this against your EXISTING dom_agent database to add hybrid-agent
-- support without losing your current data.
-- psql -U dom_agent_user -d dom_agent -h localhost -f migrate_add_hybrid.sql

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS agent_type TEXT NOT NULL DEFAULT 'baseline';

ALTER TABLE steps ADD COLUMN IF NOT EXISTS confidence REAL;
ALTER TABLE steps ADD COLUMN IF NOT EXISTS used_screenshot BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE steps ADD COLUMN IF NOT EXISTS screenshot_path TEXT;
