"""
Central configuration for the DOM-only baseline agent.
Loads from a .env file (copy env.example -> .env and fill in real values).
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

PG_CONFIG = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": int(os.getenv("PGPORT", "5432")),
    "dbname": os.getenv("PGDATABASE", "dom_agent"),
    "user": os.getenv("PGUSER", "dom_agent_user"),
    "password": os.getenv("PGPASSWORD", ""),
}

MAX_STEPS = int(os.getenv("MAX_STEPS", "25"))
NAV_TIMEOUT_MS = int(os.getenv("NAV_TIMEOUT_MS", "15000"))
HEADLESS = _bool("HEADLESS", True)
GEMINI_MIN_INTERVAL_SECONDS = float(os.getenv("GEMINI_MIN_INTERVAL_SECONDS", "4.5"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.6"))

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Copy env.example to .env and add your key."
    )
