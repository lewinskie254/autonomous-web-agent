"""
Computes the Chapter 3 "Performance Metrics" directly from Postgres,
broken down by agent_type ('baseline' vs 'hybrid') so the two can be
compared side by side: task success rate, completion time, navigation
efficiency (steps), error rate, generalization by website, and (hybrid
only) how often the screenshot fallback fired and what it cost in time.

Run standalone: python metrics.py
"""
import json

from db import get_connection


def _summary_for_agent(cur, agent_type: str) -> dict:
    cur.execute(
        "SELECT COUNT(*) FROM tasks WHERE status != 'running' AND agent_type = %s",
        (agent_type,),
    )
    total_tasks = cur.fetchone()[0]

    cur.execute(
        "SELECT COUNT(*) FROM tasks WHERE success = TRUE AND agent_type = %s",
        (agent_type,),
    )
    successful = cur.fetchone()[0]

    cur.execute(
        "SELECT AVG(duration_ms) FROM tasks WHERE status != 'running' AND agent_type = %s",
        (agent_type,),
    )
    avg_duration_ms = cur.fetchone()[0]

    cur.execute(
        "SELECT AVG(total_steps) FROM tasks WHERE status != 'running' AND agent_type = %s",
        (agent_type,),
    )
    avg_steps = cur.fetchone()[0]

    cur.execute(
        """
        SELECT COUNT(*) FROM errors e
        JOIN tasks t ON t.id = e.task_id
        WHERE t.agent_type = %s
        """,
        (agent_type,),
    )
    total_errors = cur.fetchone()[0]

    cur.execute(
        """
        SELECT t.website_name, COUNT(*) AS total,
               SUM(CASE WHEN t.success THEN 1 ELSE 0 END) AS successes
        FROM tasks t
        WHERE t.status != 'running' AND t.agent_type = %s AND t.website_name IS NOT NULL
        GROUP BY t.website_name
        """,
        (agent_type,),
    )
    per_website = [
        {
            "website": row[0],
            "total_tasks": row[1],
            "success_rate_pct": round(100 * row[2] / row[1], 1) if row[1] else None,
        }
        for row in cur.fetchall()
    ]

    result = {
        "agent_type": agent_type,
        "total_tasks": total_tasks,
        "success_rate_pct": round(100 * successful / total_tasks, 1) if total_tasks else None,
        "avg_completion_time_ms": round(avg_duration_ms, 1) if avg_duration_ms else None,
        "avg_steps_per_task": round(avg_steps, 2) if avg_steps else None,
        "total_errors": total_errors,
        "error_rate_per_task": round(total_errors / total_tasks, 2) if total_tasks else None,
        "generalization_by_website": per_website,
    }

    if agent_type == "hybrid":
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE s.used_screenshot) AS screenshot_steps,
                COUNT(*) AS total_steps,
                AVG(s.step_duration_ms) FILTER (WHERE s.used_screenshot) AS avg_screenshot_step_ms,
                AVG(s.step_duration_ms) FILTER (WHERE NOT s.used_screenshot) AS avg_dom_only_step_ms,
                AVG(s.confidence) AS avg_confidence
            FROM steps s
            JOIN tasks t ON t.id = s.task_id
            WHERE t.agent_type = 'hybrid'
            """
        )
        row = cur.fetchone()
        screenshot_steps, total_steps, avg_ss_ms, avg_dom_ms, avg_conf = row
        result["screenshot_fallback"] = {
            "screenshot_steps": screenshot_steps,
            "total_steps": total_steps,
            "screenshot_usage_pct": round(100 * screenshot_steps / total_steps, 1) if total_steps else None,
            "avg_step_duration_ms_with_screenshot": round(avg_ss_ms, 1) if avg_ss_ms else None,
            "avg_step_duration_ms_dom_only": round(avg_dom_ms, 1) if avg_dom_ms else None,
            "avg_confidence": round(avg_conf, 3) if avg_conf else None,
        }

    return result


def compute_summary() -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            baseline = _summary_for_agent(cur, "baseline")
            hybrid = _summary_for_agent(cur, "hybrid")
        return {"baseline": baseline, "hybrid": hybrid}
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(compute_summary(), indent=2))
