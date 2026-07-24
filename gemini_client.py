"""
Calls Gemini's generateContent REST endpoint directly with `requests`,
per your preference to avoid an SDK dependency. Sends the system prompt,
task, current URL, DOM element JSON, and action history; parses the model's
JSON action out of the response text.
"""
import base64
import json
import logging
import re
import time

import requests

from config import GEMINI_API_KEY, GEMINI_ENDPOINT, GEMINI_MIN_INTERVAL_SECONDS
from actions import SYSTEM_PROMPT, VALID_ACTIONS, VISION_ADDENDUM

log = logging.getLogger("gemini_client")

# Gemini occasionally returns these transiently (overloaded / rate limited).
# Retry with exponential backoff instead of failing the whole task run.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 2

# Proactive throttle: space out calls so we stay under free-tier rate limits
# instead of relying solely on reactive 429 retries (which stack up fast on
# a fast-moving agent loop). Tune via GEMINI_MIN_INTERVAL_SECONDS in .env.
_last_call_time = 0.0


def _throttle():
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    wait = GEMINI_MIN_INTERVAL_SECONDS - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_call_time = time.monotonic()


class GeminiDecisionError(Exception):
    pass


def _build_user_prompt(task: str, url: str, dom_elements: list, history: list) -> str:
    history_lines = []
    for i, h in enumerate(history[-5:], 1):  # keep prompt bounded
        history_lines.append(
            f"{i}. action={h['action']} ref={h.get('ref')} value={h.get('value')!r} "
            f"-> {h.get('outcome', 'ok')}"
        )
    history_text = "\n".join(history_lines) if history_lines else "(none yet)"

    # Compact separators (no spaces after , or :) shave a meaningful chunk
    # off the token count on pages with many elements.
    dom_json = json.dumps(dom_elements, separators=(",", ":"))

    return (
        f"TASK: {task}\n\n"
        f"CURRENT URL: {url}\n\n"
        f"DOM ELEMENTS (JSON):\n{dom_json}\n\n"
        f"HISTORY:\n{history_text}\n\n"
        f"Choose the next action as a single JSON object per the schema."
    )


def _extract_json(raw_text: str) -> dict:
    # Strip accidental markdown fences if the model adds them anyway.
    cleaned = re.sub(r"^```(json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()

    start = cleaned.find("{")
    if start == -1:
        raise GeminiDecisionError(f"No JSON object found in Gemini response: {raw_text!r}")

    # raw_decode parses only the first valid JSON value starting at `start`
    # and explicitly ignores anything trailing it (extra prose, a second
    # object, stray newlines, etc.) instead of choking on it like a naive
    # json.loads() or a greedy regex would.
    decoder = json.JSONDecoder()
    try:
        obj, _end_index = decoder.raw_decode(cleaned, start)
    except json.JSONDecodeError as e:
        raise GeminiDecisionError(
            f"Could not parse JSON from Gemini response: {raw_text!r} ({e})"
        ) from e

    if not isinstance(obj, dict):
        raise GeminiDecisionError(f"Gemini JSON was not an object: {obj!r}")

    return obj


RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "reasoning": {"type": "STRING"},
        "action": {
            "type": "STRING",
            "enum": ["click", "type", "navigate", "scroll", "go_back", "wait", "done"],
        },
        "ref": {"type": "INTEGER"},
        "value": {"type": "STRING"},
        "result": {"type": "STRING"},
        "confidence": {"type": "NUMBER"},
    },
    "required": ["reasoning", "action", "confidence"],
}


def _call_gemini(payload: dict) -> dict:
    """
    POSTs to Gemini with throttling and retry/backoff on transient errors.
    Returns the parsed response JSON, or raises GeminiDecisionError.
    """
    data = None
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _throttle()
            resp = requests.post(
                GEMINI_ENDPOINT,
                params={"key": GEMINI_API_KEY},
                json=payload,
                timeout=30,
            )
            if resp.status_code in RETRYABLE_STATUS_CODES:
                wait = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                log.warning(
                    "Gemini returned %s (attempt %d/%d), retrying in %ds...",
                    resp.status_code, attempt, MAX_RETRIES, wait,
                )
                last_error = requests.exceptions.HTTPError(
                    f"{resp.status_code} error from Gemini", response=resp
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            break

        except requests.exceptions.RequestException as e:
            # Network-level issues (timeouts, connection resets) are also
            # worth a retry rather than killing the whole task.
            last_error = e
            wait = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            log.warning(
                "Gemini request failed (%s), attempt %d/%d, retrying in %ds...",
                e, attempt, MAX_RETRIES, wait,
            )
            time.sleep(wait)

    if data is None:
        raise GeminiDecisionError(
            f"Gemini API failed after {MAX_RETRIES} attempts: {last_error}"
        )
    return data


def decide_next_action(
    task: str,
    url: str,
    dom_elements: list,
    history: list,
    screenshot_png_bytes: bytes = None,
) -> tuple:
    """
    Returns (action_dict, raw_response_text).
    Raises GeminiDecisionError on malformed/invalid responses.

    If `screenshot_png_bytes` is provided, this becomes a multimodal call:
    the same DOM JSON + task + history is sent alongside the screenshot, and
    the model is told (via VISION_ADDENDUM) to use both together for its
    final decision. This is the hybrid agent's confidence-escalation path;
    the baseline agent never passes a screenshot, so the two agents share
    byte-for-byte identical prompts on their normal (non-escalated) calls,
    per the "identical prompts" fairness requirement.
    """
    user_prompt = _build_user_prompt(task, url, dom_elements, history)

    parts = []
    if screenshot_png_bytes is not None:
        user_prompt += VISION_ADDENDUM
    parts.append({"text": user_prompt})
    if screenshot_png_bytes is not None:
        b64_image = base64.b64encode(screenshot_png_bytes).decode("ascii")
        parts.append({"inline_data": {"mime_type": "image/png", "data": b64_image}})

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "response_mime_type": "application/json",
            "response_schema": RESPONSE_SCHEMA,
            "maxOutputTokens": 500,
            # gemini-3.5-flash is a "thinking" model whose internal reasoning
            # tokens count against maxOutputTokens. We don't need deep
            # reasoning for a short structured action choice, and leaving
            # thinking on was silently eating the whole output budget
            # (finishReason=MAX_TOKENS with empty content). Disabling it
            # fixes that AND reduces token cost.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    data = _call_gemini(payload)

    try:
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise GeminiDecisionError(f"Unexpected Gemini response shape: {data}") from e

    action = _extract_json(raw_text)

    if action.get("action") not in VALID_ACTIONS:
        raise GeminiDecisionError(f"Gemini returned an invalid action: {action}")

    if action["action"] in ("click", "type"):
        valid_refs = {el["ref"] for el in dom_elements}
        if action.get("ref") not in valid_refs:
            raise GeminiDecisionError(
                f"Gemini referenced ref {action.get('ref')} not present in DOM list"
            )

    return action, raw_text
