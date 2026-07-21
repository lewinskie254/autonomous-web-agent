"""
Computes the Chapter 3 "Performance Metrics" directly from the Postgres
tables: task success rate, mean completion time, navigation efficiency
(mean steps per task), error rate, and a per-website generalization score.

Run standalone: python metrics.py
"""
import json

from db import get_connection


def compute_summary() -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM tasks WHERE status != 'running'")
            total_tasks = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM tasks WHERE success = TRUE")
            successful = cur.fetchone()[0]

            cur.execute("SELECT AVG(duration_ms) FROM tasks WHERE status != 'running'")
            avg_duration_ms = cur.fetchone()[0]

            cur.execute("SELECT AVG(total_steps) FROM tasks WHERE status != 'running'")
            avg_steps = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM errors")
            total_errors = cur.fetchone()[0]

            cur.execute(
                """
                SELECT website_name,
                       COUNT(*) AS total,
                       SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successes
                FROM tasks
                WHERE status != 'running' AND website_name IS NOT NULL
                GROUP BY website_name
                """
            )
            per_website = [
                {
                    "website": row[0],
                    "total_tasks": row[1],
                    "success_rate_pct": round(100 * row[2] / row[1], 1) if row[1] else None,
                }
                for row in cur.fetchall()
            ]

        success_rate_pct = round(100 * successful / total_tasks, 1) if total_tasks else None
        error_rate_per_task = round(total_errors / total_tasks, 2) if total_tasks else None

        return {
            "total_tasks": total_tasks,
            "success_rate_pct": success_rate_pct,
            "avg_completion_time_ms": round(avg_duration_ms, 1) if avg_duration_ms else None,
            "avg_steps_per_task": round(avg_steps, 2) if avg_steps else None,
            "total_errors": total_errors,
            "error_rate_per_task": error_rate_per_task,
            "generalization_by_website": per_website,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(compute_summary(), indent=2))
