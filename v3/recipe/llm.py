"""One small, text-only Gemini call used by a recipe to make the decision the AI used to make step by step."""

import asyncio
import json
import logging
import re
from typing import Any

from google import genai
from google.genai import types

from keypool import mask, models_for, pool
from run_context import RunContext

logger = logging.getLogger(__name__)

_DAILY_SIGNATURE = "GenerateRequestsPerDayPerProjectPerModel"
_AUTH_SIGNATURES = ("API key not valid", "API_KEY_INVALID", "PERMISSION_DENIED", "UNAUTHENTICATED")


# Raised when the model call cannot succeed for the whole batch (daily quota used up or the key is rejected).
class LlmHalt(Exception):
    def __init__(self, message: str, *, quota: bool):
        super().__init__(message)
        self.quota = quota


# Asks the model for a JSON answer to a prompt (optionally forced into a schema), using the run's keys in turn: a busy or used-up key is set aside and
# the call goes to another one; with no key free it waits for the soonest one. Every call is booked in its key's per-minute window
# first (keypool.RPM), so many browsers together stay under Gemini's per-minute limit instead of running into "429" answers.
# Takes the RunContext and the prompt text; returns the parsed JSON object.
async def ask_json(ctx: RunContext, prompt: str, schema: Any = None, temperature: float = 0.0) -> dict[str, Any]:
    keys = ctx.all_gemini_keys
    models = models_for(ctx.model_name)
    # With a schema the model must answer in exactly that shape (small models sometimes drop a list down to one item otherwise)
    config = types.GenerateContentConfig(response_mime_type="application/json", temperature=temperature, **({"response_schema": schema} if schema is not None else {}))
    last: Exception | None = None
    bad_answers = 0
    server_errors = 0
    for _attempt in range(20):
        model = pool.first_open_model(keys, models)
        if model is None:
            raise LlmHalt("Gemini's daily free-tier quota is exhausted on every key.", quota=True)
        # Waits (outside any lock) when every usable key has already made its share of calls this minute
        key = await pool.paced_pick(keys, model)
        if key is None:
            wait = pool.wait_seconds(keys, model)
            await asyncio.sleep(min(wait if wait is not None else 5, 30) + 0.5)
            continue
        try:
            client = genai.Client(api_key=key)
            response = await client.aio.models.generate_content(model=model, contents=prompt, config=config)
            text = (response.text or "").strip()
            text = re.sub(r"^```(?:json)?|```$", "", text).strip()
            data = json.loads(text)
            if isinstance(data, list) and data and isinstance(data[0], dict):
                data = data[0]
            if not isinstance(data, dict):
                raise ValueError("the model did not return a JSON object")
            return data
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            last = exc
            if _DAILY_SIGNATURE in message:
                pool.exhaust_today(key, model)
                logger.warning("Gemini key %s is out of daily quota for %s; switching", mask(key), model)
                continue
            if any(sig in message for sig in _AUTH_SIGNATURES):
                raise LlmHalt("Gemini rejected an API key (or it has no access to this model).", quota=False) from exc
            if "429" in message or "RESOURCE_EXHAUSTED" in message:
                pool.cool(key, model)
                logger.warning("Gemini key %s is rate-limited; switching keys", mask(key))
                continue
            if ("503" in message or "500" in message or "UNAVAILABLE" in message) and server_errors < 6:
                server_errors += 1
                await asyncio.sleep(min(2**server_errors, 30))  # busy servers usually recover within a minute
                continue
            if isinstance(exc, (json.JSONDecodeError, ValueError)) and bad_answers < 2:
                bad_answers += 1
                continue
            break
    raise RuntimeError(f"the model call failed: {str(last)[:200]}")
