"""Infrastructure settings only. Per-run settings live in run_context.RunContext."""

import os
from dotenv import load_dotenv

load_dotenv()

# PostgreSQL connection string for the app's own data (secrets, workflows, runs, logs, learned steps).
# Required; checked when the app first touches the database, with a message that says how to get one.
DATABASE_URL: str | None = os.getenv("DATABASE_URL")

# When set (for a server with no screen), every browser runs headless whatever a task asks for
FORCE_HEADLESS: bool = os.getenv("FORCE_HEADLESS", "").lower() in ("1", "true", "yes")

# Master key for encrypting stored secrets (a Fernet key); when unset, one is created at MASTER_KEY_PATH
SECRETS_ENCRYPTION_KEY: str | None = os.getenv("SECRETS_ENCRYPTION_KEY")
MASTER_KEY_PATH: str = os.getenv("MASTER_KEY_PATH", "storage/master.key")

# The Gemini model every task uses unless a saved task says otherwise (users do not choose one)
DEFAULT_MODEL: str = os.getenv("MODEL_NAME", "gemini-3.5-flash-lite")
