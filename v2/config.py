"""Loads app configuration from environment variables (.env via python-dotenv)."""

import os
from dotenv import load_dotenv

load_dotenv()


# Reads a required environment variable by name and returns its value.
# Raises RuntimeError pointing at .env.example if the variable is missing or empty.
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

# Gemini model name
MODEL_NAME: str = os.getenv("MODEL_NAME", "gemini-3.5-flash-lite")

# THINKING_LEVEL is unused for now; kept as a placeholder for future thinking-effort tuning
# THINKING_LEVEL: str = os.getenv("THINKING_LEVEL", "low")

# Field used to match a source Record to a row in Supabase
MATCH_KEY_FIELD: str = os.getenv("MATCH_KEY_FIELD", "email")

# Results storage (flat JSON file for now)
RESULTS_STORE_PATH: str = os.getenv("RESULTS_STORE_PATH", "storage/results.json")

# --- Caching layer ---

# SQLite file holding the replay cache
CACHE_DB_PATH: str = os.getenv("CACHE_DB_PATH", "storage/cache.sqlite3")

# Identifies which cached workflow a batch belongs to (one per form/flow)
WORKFLOW_NAME: str = os.getenv("WORKFLOW_NAME", "datadesk_people_form")

# Port of the shared Chrome's CDP endpoint, used by the live agent to attach
CDP_DEBUG_PORT: int = int(os.getenv("CDP_DEBUG_PORT", "9333"))

# Wait time for an element to become actionable before falling back to the live agent
REPLAY_ELEMENT_TIMEOUT_MS: int = int(os.getenv("REPLAY_ELEMENT_TIMEOUT_MS", "5000"))

# Max Chrome windows open at once during replay (RAM is the limit, not API quota)
MAX_CONCURRENT_REPLAYS: int = int(os.getenv("MAX_CONCURRENT_REPLAYS", "10"))
