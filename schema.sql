-- Schema for the DOM-only baseline agent's experimental data store.
-- Run once against a fresh database: psql -d dom_agent -f schema.sql

CREATE TABLE IF NOT EXISTS tasks (
    id              SERIAL PRIMARY KEY,
    task_description TEXT NOT NULL,
    start_url       TEXT NOT NULL,
    website_name    TEXT,                       -- e.g. "shopify_store_a" (for generalization grouping)
    status          TEXT NOT NULL DEFAULT 'running', -- running | success | failure | error
    final_result    TEXT,                        -- text the agent returns when it signals DONE
    total_steps     INTEGER DEFAULT 0,
    success         BOOLEAN,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    duration_ms     INTEGER
);

CREATE TABLE IF NOT EXISTS steps (
    id                 SERIAL PRIMARY KEY,
    task_id            INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_number        INTEGER NOT NULL,
    url                TEXT,
    dom_snapshot       JSONB,       -- the interactive-element JSON sent to Gemini
    dom_element_count  INTEGER,
    gemini_prompt      TEXT,        -- the user/task portion of the prompt sent (system prompt is static, not duplicated)
    gemini_raw_response TEXT,       -- raw text Gemini returned
    action_type        TEXT,        -- click | type | navigate | scroll | go_back | done | error
    action_payload     JSONB,       -- parsed action: {ref, value, url, ...}
    step_duration_ms   INTEGER,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS errors (
    id            SERIAL PRIMARY KEY,
    task_id       INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_id       INTEGER REFERENCES steps(id) ON DELETE SET NULL,
    error_type    TEXT NOT NULL,  -- invalid_action | element_not_found | timeout | navigation_loop | gemini_parse_error | other
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_steps_task_id ON steps(task_id);
CREATE INDEX IF NOT EXISTS idx_errors_task_id ON errors(task_id);
