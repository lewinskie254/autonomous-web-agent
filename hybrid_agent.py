"""
The hybrid agent's main control loop, per Chapter 3's design.

    extract_dom -> decide_next_action (DOM-only, Gemini) -> if confidence
    < CONFIDENCE_THRESHOLD: screenshot -> decide_next_action AGAIN (DOM +
    screenshot, multimodal) -> execute_action (Playwright) -> log_step
    (Postgres, records confidence + whether the screenshot fallback fired)
    -> repeat until "done" or MAX_STEPS.

Everything else (DOM extraction, executor, safety guardrail, Postgres
logging, retry/throttle behaviour, MAX_STEPS/NAV_TIMEOUT_MS/HEADLESS) is
byte-for-byte shared with the baseline agent (agent.py), and the system
prompt/response schema Gemini sees on the DOM-only call is identical between
the two agents. The *only* difference is this confidence-gated escalation
step -- which is exactly what Chapter 3's "Experimental Environment"
fairness requirement calls for when comparing the two.
"""
import logging
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import MAX_STEPS, HEADLESS, NAV_TIMEOUT_MS, CONFIDENCE_THRESHOLD
from dom_extractor import extract_dom
from gemini_client import decide_next_action, GeminiDecisionError
from executor import execute_action, ActionExecutionError
from safety import check_click_safety
from db import TaskLogger

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hybrid_agent")

SCREENSHOT_DIR = Path("screenshots")


def run_task(task_description: str, start_url: str, website_name: str = None) -> dict:
    """
    Runs a single task end-to-end with the hybrid (DOM + confidence-gated
    vision fallback) agent. Returns a summary dict:
    {task_id, success, steps, final_result, duration_ms, screenshot_calls}
    """
    log.info("Starting HYBRID task: %r", task_description)
    log.info("Start URL: %s", start_url)
    log.info("Confidence threshold for screenshot escalation: %.2f", CONFIDENCE_THRESHOLD)

    logger = TaskLogger(task_description, start_url, website_name=website_name, agent_type="hybrid")
    task_id = logger.start()
    log.info("Task logged in Postgres as task_id=%s (agent_type=hybrid)", task_id)

    task_screenshot_dir = SCREENSHOT_DIR / str(task_id)

    history = []
    success = False
    final_result = None
    screenshot_calls = 0
    start_time = time.time()

    with sync_playwright() as p:
        log.info("Launching Chromium (headless=%s)...", HEADLESS)
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        page.set_default_timeout(NAV_TIMEOUT_MS)

        try:
            log.info("Navigating to start URL (timeout=%sms)...", NAV_TIMEOUT_MS)
            page.goto(start_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            log.info("Loaded: %s", page.url)
        except Exception as e:
            log.error("FAILED to load start_url: %s", e)
            logger.log_error("navigation_failed", str(e))
            logger.finish(success=False, final_result=f"Could not load start_url: {e}")
            browser.close()
            return {
                "task_id": task_id, "success": False, "steps": 0,
                "final_result": None, "duration_ms": int((time.time() - start_time) * 1000),
                "screenshot_calls": 0,
            }

        for step_number in range(1, MAX_STEPS + 1):
            log.info("--- Step %d/%d ---", step_number, MAX_STEPS)
            step_start = time.time()

            dom_elements = extract_dom(page)
            current_url = page.url
            log.info("URL: %s | %d interactive elements found", current_url, len(dom_elements))

            # --- Pass 1: DOM-only decision (identical to the baseline agent) ---
            try:
                log.info("Asking Gemini for next action (DOM-only)...")
                action, raw_response = decide_next_action(
                    task_description, current_url, dom_elements, history
                )
            except GeminiDecisionError as e:
                log.warning("Gemini decision error: %s", e)
                logger.log_error("gemini_parse_error", str(e))
                history.append({"action": "error", "outcome": f"gemini error: {e}"})
                continue

            confidence = action.get("confidence")
            used_screenshot = False
            screenshot_path = None

            log.info(
                "DOM-only decision: action=%s ref=%s confidence=%s | reasoning: %s",
                action["action"], action.get("ref"), confidence, action.get("reasoning", ""),
            )

            # --- Pass 2 (only if triggered): screenshot + multimodal re-decision ---
            if (
                action["action"] != "done"
                and confidence is not None
                and confidence < CONFIDENCE_THRESHOLD
            ):
                log.info(
                    "Confidence %.2f < threshold %.2f -> escalating to screenshot fallback",
                    confidence, CONFIDENCE_THRESHOLD,
                )
                try:
                    screenshot_bytes = page.screenshot(type="png")
                    task_screenshot_dir.mkdir(parents=True, exist_ok=True)
                    screenshot_path = str(task_screenshot_dir / f"step_{step_number}.png")
                    with open(screenshot_path, "wb") as f:
                        f.write(screenshot_bytes)

                    vision_action, vision_raw = decide_next_action(
                        task_description, current_url, dom_elements, history,
                        screenshot_png_bytes=screenshot_bytes,
                    )
                    log.info(
                        "Vision-augmented decision: action=%s ref=%s | reasoning: %s",
                        vision_action["action"], vision_action.get("ref"), vision_action.get("reasoning", ""),
                    )
                    action = vision_action
                    raw_response = vision_raw
                    used_screenshot = True
                    screenshot_calls += 1
                except GeminiDecisionError as e:
                    log.warning("Vision escalation failed, falling back to DOM-only decision: %s", e)
                    logger.log_error("gemini_vision_error", str(e))
                    # Keep the original DOM-only `action` as the fallback.

            gemini_prompt_summary = f"task={task_description!r} url={current_url}"

            if action["action"] == "done":
                success = True
                final_result = action.get("result", "")
                log.info("Agent signaled DONE. Result: %s", final_result)
                step_duration_ms = int((time.time() - step_start) * 1000)
                logger.log_step(
                    step_number, current_url, dom_elements, gemini_prompt_summary,
                    raw_response, "done", action, step_duration_ms,
                    confidence=action.get("confidence"), used_screenshot=used_screenshot,
                    screenshot_path=screenshot_path,
                )
                history.append({"action": "done", "outcome": "task complete"})
                break

            # --- Safety guardrail (identical to the baseline agent) ---
            safety_verdict = None
            matched_text = None
            if action["action"] == "click":
                safety_verdict, matched_text = check_click_safety(dom_elements, action.get("ref"))

            if safety_verdict == "block":
                log.warning(
                    "SAFETY: refusing to click a purchase-completing element (matched %r). Halting task.",
                    matched_text,
                )
                step_duration_ms = int((time.time() - step_start) * 1000)
                step_id = logger.log_step(
                    step_number, current_url, dom_elements, gemini_prompt_summary,
                    raw_response, "blocked", action, step_duration_ms,
                    confidence=action.get("confidence"), used_screenshot=used_screenshot,
                    screenshot_path=screenshot_path,
                )
                logger.log_error(
                    "blocked_purchase_action",
                    f"Refused to click element matching purchase keywords: {matched_text!r}",
                    step_id=step_id,
                )
                success = False
                final_result = (
                    f"Stopped for safety: the next click ({matched_text!r}) looked like it would "
                    "finalize a purchase, so the agent refused to proceed."
                )
                break

            try:
                outcome = execute_action(page, action)
                step_error = None
                log.info("Executed action -> %s", outcome)
            except ActionExecutionError as e:
                outcome = f"failed: {e}"
                step_error = str(e)
                log.warning("Action execution FAILED: %s", e)

            step_duration_ms = int((time.time() - step_start) * 1000)
            step_id = logger.log_step(
                step_number, current_url, dom_elements, gemini_prompt_summary,
                raw_response, action["action"], action, step_duration_ms,
                confidence=action.get("confidence"), used_screenshot=used_screenshot,
                screenshot_path=screenshot_path,
            )

            if step_error:
                logger.log_error("element_action_failed", step_error, step_id=step_id)

            if safety_verdict == "stop_after" and not step_error:
                log.info(
                    "SAFETY: add-to-cart action detected (matched %r). Halting task here to avoid checkout.",
                    matched_text,
                )
                success = True
                final_result = f"Added item to cart (matched {matched_text!r}). Stopped here as a safety measure before checkout."
                history.append({"action": action["action"], "ref": action.get("ref"), "value": action.get("value"), "outcome": outcome})
                break

            history.append(
                {
                    "action": action["action"],
                    "ref": action.get("ref"),
                    "value": action.get("value"),
                    "outcome": outcome,
                }
            )
        else:
            final_result = "Max steps reached without completing the task."
            log.warning(final_result)
            logger.log_error("max_steps_reached", final_result)

        browser.close()
        log.info("Browser closed.")

    duration_ms = int((time.time() - start_time) * 1000)
    logger.finish(success=success, final_result=final_result)
    log.info(
        "Task finished. success=%s duration=%dms steps=%d screenshot_calls=%d",
        success, duration_ms, logger.step_count, screenshot_calls,
    )

    return {
        "task_id": task_id,
        "success": success,
        "steps": logger.step_count,
        "final_result": final_result,
        "duration_ms": duration_ms,
        "screenshot_calls": screenshot_calls,
    }
