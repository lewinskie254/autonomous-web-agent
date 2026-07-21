"""
Executes a single decided action against the live Playwright page.
Every element the agent can target was tagged with data-agent-ref by
dom_extractor.extract_dom(), so we always select via that stable attribute
rather than trusting the site's own (often meaningless) ids/classes.
"""
from config import NAV_TIMEOUT_MS


class ActionExecutionError(Exception):
    pass


def _locator_for_ref(page, ref: int):
    locator = page.locator(f"[data-agent-ref='{ref}']")
    if locator.count() == 0:
        raise ActionExecutionError(f"No element found for ref={ref} (page may have changed)")
    return locator.first


def execute_action(page, action: dict) -> str:
    """
    Executes the action on the page. Returns a short human-readable outcome
    string that gets fed back into the next Gemini prompt as history context.
    """
    kind = action["action"]

    try:
        if kind == "click":
            el = _locator_for_ref(page, action["ref"])
            el.scroll_into_view_if_needed(timeout=NAV_TIMEOUT_MS)
            el.click(timeout=NAV_TIMEOUT_MS)
            page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
            return "clicked"

        elif kind == "type":
            el = _locator_for_ref(page, action["ref"])
            el.scroll_into_view_if_needed(timeout=NAV_TIMEOUT_MS)
            el.fill(action.get("value", ""), timeout=NAV_TIMEOUT_MS)
            return "typed"

        elif kind == "navigate":
            url = action.get("value")
            if not url:
                raise ActionExecutionError("navigate action missing 'value' URL")
            page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            return "navigated"

        elif kind == "scroll":
            direction = action.get("value", "down")
            delta = 800 if direction == "down" else -800
            page.mouse.wheel(0, delta)
            page.wait_for_timeout(300)
            return f"scrolled {direction}"

        elif kind == "go_back":
            page.go_back(timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            return "went back"

        elif kind == "wait":
            page.wait_for_timeout(1000)
            return "waited"

        elif kind == "done":
            return "task marked done"

        else:
            raise ActionExecutionError(f"Unknown action type: {kind}")

    except Exception as e:
        raise ActionExecutionError(str(e)) from e
