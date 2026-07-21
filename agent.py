"""
The baseline DOM-only agent's main control loop.

    extract_dom -> decide_next_action (Gemini) -> execute_action (Playwright)
    -> log_step (Postgres) -> repeat until Gemini emits "done" or MAX_STEPS
    is reached.

This is intentionally kept separate from the (future) hybrid agent's loop,
which will add a screenshot fallback when Gemini's confidence is low, so the
two can be compared under identical conditions per Chapter 3.
"""
import time

from playwright.sync_api import sync_playwright

from config import MAX_STEPS, HEADLESS, NAV_TIMEOUT_MS
from dom_extractor import extract_dom
from gemini_client import decide_next_action, GeminiDecisionError
from executor import execute_action, ActionExecutionError
from db import TaskLogger


def run_task(task_description: str, start_url: str, website_name: str = None) -> dict:
    """
    Runs a single task end-to-end and returns a summary dict:
    {task_id, success, steps, final_result, duration_ms}
    """
    logger = TaskLogger(task_description, start_url, website_name=website_name)
    task_id = logger.start()

    history = []
    success = False
    final_result = None
    start_time = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        page.set_default_timeout(NAV_TIMEOUT_MS)

        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            logger.log_error("navigation_failed", str(e))
            logger.finish(success=False, final_result=f"Could not load start_url: {e}")
            browser.close()
            return {
                "task_id": task_id,
                "success": False,
                "steps": 0,
                "final_result": None,
                "duration_ms": int((time.time() - start_time) * 1000),
            }

        for step_number in range(1, MAX_STEPS + 1):
            step_start = time.time()
            dom_elements = extract_dom(page)
            current_url = page.url

            try:
                action, raw_response = decide_next_action(
                    task_description, current_url, dom_elements, history
                )
            except GeminiDecisionError as e:
                logger.log_error("gemini_parse_error", str(e))
                history.append({"action": "error", "outcome": f"gemini error: {e}"})
                continue

            gemini_prompt_summary = f"task={task_description!r} url={current_url}"

            if action["action"] == "done":
                success = True
                final_result = action.get("result", "")
                step_duration_ms = int((time.time() - step_start) * 1000)
                logger.log_step(
                    step_number, current_url, dom_elements, gemini_prompt_summary,
                    raw_response, "done", action, step_duration_ms,
                )
                history.append({"action": "done", "outcome": "task complete"})
                break

            try:
                outcome = execute_action(page, action)
                step_error = None
            except ActionExecutionError as e:
                outcome = f"failed: {e}"
                step_error = str(e)

            step_duration_ms = int((time.time() - step_start) * 1000)
            step_id = logger.log_step(
                step_number, current_url, dom_elements, gemini_prompt_summary,
                raw_response, action["action"], action, step_duration_ms,
            )

            if step_error:
                logger.log_error("element_action_failed", step_error, step_id=step_id)

            history.append(
                {
                    "action": action["action"],
                    "ref": action.get("ref"),
                    "value": action.get("value"),
                    "outcome": outcome,
                }
            )
        else:
            # Loop exhausted MAX_STEPS without "done"
            final_result = "Max steps reached without completing the task."
            logger.log_error("max_steps_reached", final_result)

        browser.close()

    duration_ms = int((time.time() - start_time) * 1000)
    logger.finish(success=success, final_result=final_result)

    return {
        "task_id": task_id,
        "success": success,
        "steps": logger.step_count,
        "final_result": final_result,
        "duration_ms": duration_ms,
    }
