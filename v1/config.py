"""
Loads all configuration from environment variables (.env file via python-dotenv).
Never hardcode secrets here — this file just reads them.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Check your .env file against .env.example."
        )
    return value


# --- Secrets / credentials ---
GEMINI_API_KEY: str = _require("GEMINI_API_KEY")
SUPABASE_URL: str = _require("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY: str = _require("SUPABASE_SERVICE_ROLE_KEY")

# --- App config ---
TARGET_FORM_URL: str = _require("TARGET_FORM_URL")
SUPABASE_TABLE: str = os.getenv("SUPABASE_TABLE", "records")

# Model name — Gemini 3.5 Flash-Lite
MODEL_NAME: str = os.getenv("MODEL_NAME", "gemini-3.5-flash-lite")

# Per-field-type "thinking effort" knob, wired up later if needed.
THINKING_LEVEL: str = os.getenv("THINKING_LEVEL", "low")

# Field used to match a source Record to a row read back from Supabase.
# Override in .env if email isn't the right unique key for your form.
MATCH_KEY_FIELD: str = os.getenv("MATCH_KEY_FIELD", "email")

# Results storage (flat JSON file for now)
RESULTS_STORE_PATH: str = os.getenv("RESULTS_STORE_PATH", "storage/results.json")

# v1 (sequential, one record at a time) is the default/safe mode -- see
# LEARNINGS.md for why concurrency blew through the Gemini free-tier rate
# limit last time. Set CONCURRENT_MODE=true in .env to switch to v2
# (every record at once, each in its own isolated Chrome window) when
# there's enough quota headroom for it; unset/false goes straight back to
# v1 with no code changes needed.
CONCURRENT_MODE: bool = os.getenv("CONCURRENT_MODE", "false").strip().lower() == "true"
