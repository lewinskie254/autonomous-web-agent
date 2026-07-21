"""
Thin PostgreSQL logging layer for the baseline agent's experimental data.
Matches the tables defined in schema.sql.
"""
import json
import psycopg2
import psycopg2.extras

from config import PG_CONFIG


def get_connection():
    return psycopg2.connect(**PG_CONFIG)


class TaskLogger:
    """
    One TaskLogger instance corresponds to one agent run on one task/website.
    Usage:
        logger = TaskLogger(task_description, start_url, website_name="shopify_a")
        logger.start()
        ... per step ...
        logger.log_step(step_number, url, dom_snapshot, gemini_prompt,
                         gemini_raw_response, action_type, action_payload,
                         step_duration_ms)
        ... on error ...
        logger.log_error("timeout", "element not found", step_id=step_id)
        ... at the end ...
        logger.finish(success=True, final_result="...")
    """

    def __init__(self, task_description: str, start_url: str, website_name: str = None):
        self.task_description = task_description
        self.start_url = start_url
        self.website_name = website_name
        self.task_id = None
        self.step_count = 0

    def start(self):
        conn = get_connection()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tasks (task_description, start_url, website_name, status)
                    VALUES (%s, %s, %s, 'running')
                    RETURNING id
                    """,
                    (self.task_description, self.start_url, self.website_name),
                )
                self.task_id = cur.fetchone()[0]
        finally:
            conn.close()
        return self.task_id

    def log_step(
        self,
        step_number: int,
        url: str,
        dom_snapshot: list,
        gemini_prompt: str,
        gemini_raw_response: str,
        action_type: str,
        action_payload: dict,
        step_duration_ms: int,
    ) -> int:
        conn = get_connection()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO steps (
                        task_id, step_number, url, dom_snapshot, dom_element_count,
                        gemini_prompt, gemini_raw_response, action_type, action_payload,
                        step_duration_ms
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        self.task_id,
                        step_number,
                        url,
                        json.dumps(dom_snapshot),
                        len(dom_snapshot) if dom_snapshot else 0,
                        gemini_prompt,
                        gemini_raw_response,
                        action_type,
                        json.dumps(action_payload),
                        step_duration_ms,
                    ),
                )
                step_id = cur.fetchone()[0]
        finally:
            conn.close()
        self.step_count = step_number
        return step_id

    def log_error(self, error_type: str, error_message: str, step_id: int = None):
        conn = get_connection()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO errors (task_id, step_id, error_type, error_message)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (self.task_id, step_id, error_type, error_message),
                )
        finally:
            conn.close()

    def finish(self, success: bool, final_result: str = None, status: str = None):
        final_status = status or ("success" if success else "failure")
        conn = get_connection()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = %s,
                        success = %s,
                        final_result = %s,
                        total_steps = %s,
                        finished_at = now(),
                        duration_ms = EXTRACT(EPOCH FROM (now() - created_at)) * 1000
                    WHERE id = %s
                    """,
                    (final_status, success, final_result, self.step_count, self.task_id),
                )
        finally:
            conn.close()
