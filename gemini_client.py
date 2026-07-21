"""
Calls Gemini's generateContent REST endpoint directly with `requests`,
per your preference to avoid an SDK dependency. Sends the system prompt,
task, current URL, DOM element JSON, and action history; parses the model's
JSON action out of the response text.
"""
import json
import re

import requests

from config import GEMINI_API_KEY, GEMINI_ENDPOINT
from actions import SYSTEM_PROMPT, VALID_ACTIONS


class GeminiDecisionError(Exception):
    pass


def _build_user_prompt(task: str, url: str, dom_elements: list, history: list) -> str:
    history_lines = []
    for i, h in enumerate(history[-8:], 1):  # keep prompt bounded
        history_lines.append(
            f"{i}. action={h['action']} ref={h.get('ref')} value={h.get('value')!r} "
            f"-> {h.get('outcome', 'ok')}"
        )
    history_text = "\n".join(history_lines) if history_lines else "(none yet)"

    return (
        f"TASK: {task}\n\n"
        f"CURRENT URL: {url}\n\n"
        f"DOM ELEMENTS (JSON):\n{json.dumps(dom_elements)}\n\n"
        f"HISTORY:\n{history_text}\n\n"
        f"Choose the next action as a single JSON object per the schema."
    )


def _extract_json(raw_text: str) -> dict:
    # Strip accidental markdown fences if the model adds them anyway.
    cleaned = re.sub(r"^```(json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to grabbing the first {...} block in the text.
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise GeminiDecisionError(f"Could not parse JSON from Gemini response: {raw_text!r}")
        return json.loads(match.group(0))


def decide_next_action(task: str, url: str, dom_elements: list, history: list) -> tuple:
    """
    Returns (action_dict, raw_response_text).
    Raises GeminiDecisionError on malformed/invalid responses.
    """
    user_prompt = _build_user_prompt(task, url, dom_elements, history)

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "response_mime_type": "application/json",
        },
    }

    resp = requests.post(
        GEMINI_ENDPOINT,
        params={"key": GEMINI_API_KEY},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

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
