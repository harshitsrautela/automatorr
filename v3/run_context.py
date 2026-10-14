"""Per-run settings: everything that can differ from one run (workflow) to the next, passed down explicitly."""

import os
from dataclasses import dataclass, field

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


# Shows a secret as dots plus its last four characters, so printed output stays safe to share.
# Takes the secret text and returns the masked string.
def _mask(secret: str) -> str:
    if len(secret) <= 8:
        return "••••"
    return f"••••{secret[-4:]}"


# The extra Gemini keys listed in the environment (GEMINI_API_KEY_2, GEMINI_API_KEY_3, ...), in order.
def extra_keys_from_env() -> tuple[str, ...]:
    keys = [os.getenv(f"GEMINI_API_KEY_{n}", "").strip() for n in range(2, 10)]
    return tuple(k for k in keys if k)


# Holds every setting that belongs to one run: target, workflow identity, prompt, credentials and limits.
# Built once where a run starts (from a saved workflow, or from env vars via from_env) and handed to every function that needs a setting.
@dataclass(frozen=True)
class RunContext:
    # --- What to automate ---
    target_url: str
    workflow_name: str  # namespace for this workflow's cached steps
    match_key_field: str

    # --- LLM ---
    model_name: str
    gemini_api_key: str = field(repr=False)
    # More keys for the same model; the agent switches between all of them when one is busy or out of quota
    extra_gemini_keys: tuple[str, ...] = field(default=(), repr=False)

    # --- Browser: False shows the Chrome window while it works, True runs it without a window ---
    headless: bool = False

    # --- Run limits ---
    replay_element_timeout_ms: int = 5000
    max_concurrent_replays: int = 3
    cdp_base_port: int = 9333

    # --- Task wording: the workflow's instructions for the agent (None = the built-in form-filling prompt) ---
    task_instructions: str | None = None

    # --- Caching: learn clicks/typing on the first record and replay them for the rest ---
    use_cache: bool = True

    # --- Submit signal: wait for a network request whose URL contains this text (None = don't wait) ---
    submit_url_contains: str | None = None
    submit_method: str = "POST"

    # --- Verification database (optional) ---
    supabase_table: str = ""
    supabase_url: str | None = None
    supabase_service_role_key: str | None = field(default=None, repr=False)

    # Every Gemini key this run may use, the main one first, without repeats.
    @property
    def all_gemini_keys(self) -> list[str]:
        return list(dict.fromkeys([self.gemini_api_key, *self.extra_gemini_keys]))

    # Builds a RunContext from environment variables (the .env file), for quick checks from the command line.
    # Takes nothing and returns the context; raises RuntimeError if a required variable is missing.
    @classmethod
    def from_env(cls) -> "RunContext":
        table = os.getenv("SUPABASE_TABLE", "records")
        return cls(
            target_url=_require("TARGET_FORM_URL"),
            workflow_name=os.getenv("WORKFLOW_NAME", "datadesk_people_form"),
            match_key_field=os.getenv("MATCH_KEY_FIELD", "email"),
            model_name=os.getenv("MODEL_NAME", "gemini-3.5-flash-lite"),
            gemini_api_key=_require("GEMINI_API_KEY"),
            extra_gemini_keys=extra_keys_from_env(),
            headless=os.getenv("HEADLESS", "").lower() in ("1", "true", "yes"),
            replay_element_timeout_ms=int(os.getenv("REPLAY_ELEMENT_TIMEOUT_MS", "5000")),
            max_concurrent_replays=int(os.getenv("MAX_CONCURRENT_REPLAYS", "10")),
            cdp_base_port=int(os.getenv("CDP_DEBUG_PORT", "9333")),
            submit_url_contains=f"/rest/v1/{table}",
            supabase_table=table,
            supabase_url=_require("SUPABASE_URL"),
            supabase_service_role_key=_require("SUPABASE_SERVICE_ROLE_KEY"),
        )

    # Lists every setting on its own line with secrets masked.
    # Takes nothing and returns the text, meant for printing to check what a run will use.
    def describe(self) -> str:
        lines = ["RunContext:"]
        secrets = {"gemini_api_key", "supabase_service_role_key"}
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if name == "extra_gemini_keys":
                value = f"{len(value)} more key(s)"
            elif name in secrets and value:
                value = _mask(value)
            elif name == "task_instructions" and value:
                value = value.replace("\n", " ")[:80] + ("…" if len(value) > 80 else "")
            lines.append(f"  {name:<28} = {value}")
        return "\n".join(lines)

    # Same as describe(), so print(ctx) is safe.
    def __str__(self) -> str:
        return self.describe()


# Running this file directly prints the context built from .env, to check the values by eye.
if __name__ == "__main__":
    print(RunContext.from_env())
