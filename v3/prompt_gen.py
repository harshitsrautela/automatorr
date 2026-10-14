"""Turns a user's plain-language task description into instructions for the browser agent (one LLM call)."""

import json
import time
from typing import Any

from google import genai
from google.genai import types

from keypool import pool

# The longest this call waits for room in the key's per-minute window, and how often it tries after a "too many requests"
_MAX_WAIT_SECONDS = 70.0
_TRIES = 3

_META_PROMPT = """You help configure a browser-automation agent. A user described a job in plain language.
Rewrite it as clear, step-by-step instructions for an agent that controls a web browser and does this job once per record.

You are given:
- The user's description (between <description> tags).
- The page the agent starts on.
- The fields each record has, with sample values (between <fields> tags).

Rules:
- Write instructions only. Do not include real data values or secrets.
- Refer to record data by field name (for example "the id field"), not by value.
- Say where to look things up and in what order: where to start, what to read, what to compare, what to do last.
- State exactly how each possible outcome maps to an action (which button, which value).
- Keep it short and concrete. Do not restate generic safety rules (do not invent values, wait for slow elements, do each action once); they are added separately.
- If the description is ambiguous or leaves out something the agent would need to decide (a rule for an edge case, which page to use), do not guess: add a short warning for each gap.

Return JSON only, with exactly these keys:
{"task_instructions": string, "warnings": [string]}

Starting page: %(url)s

<description>
%(description)s
</description>

<fields>
%(fields)s
</fields>
"""


# Books one call on the key in the shared key pool's per-minute window (keypool.RPM), waiting for room when the minute is
# full (or while the key cools down after a "too many requests"), so this call never takes a slot a running batch counts on.
# Takes the key and the model; raises RuntimeError when the key is set aside for the day or stays busy too long.
def _book_call(api_key: str, model: str) -> None:
    waited = 0.0
    while True:
        blocked = pool.wait_seconds([api_key], model)
        if blocked is None:
            raise RuntimeError("the Gemini key is out of its daily quota for this model")
        wait = blocked if blocked > 0 else pool.reserve(api_key, model)  # a key cooling down after a 429 waits first
        if wait <= 0:
            return
        if waited + wait > _MAX_WAIT_SECONDS:
            raise RuntimeError("the Gemini key is busy with a running batch; try again in a minute")
        time.sleep(wait + 0.2)  # a plain function (the web server runs it in a worker thread)
        waited += wait + 0.2


# Asks the model to write agent instructions from the user's description. The call is paced and booked in the same key
# pool as a batch's calls, and a "too many requests" or a daily-quota answer sets the key aside there as well.
# Takes the description, start URL, a sample record (or {}), the model name and API key; returns {"task_instructions", "warnings"}.
def generate_instructions(description: str, url: str, sample: dict[str, Any], model: str, api_key: str) -> dict[str, Any]:
    fields = "\n".join(f"- {k}: {v!r}" for k, v in sample.items()) or "(no sample data yet)"
    prompt = _META_PROMPT % {"url": url, "description": description.strip(), "fields": fields}

    client = genai.Client(api_key=api_key)
    for attempt in range(_TRIES):
        _book_call(api_key, model)
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
            )
            break
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "GenerateRequestsPerDayPerProjectPerModel" in message:
                pool.exhaust_today(api_key, model)
                raise
            if ("429" in message or "RESOURCE_EXHAUSTED" in message) and attempt + 1 < _TRIES:
                pool.cool(api_key, model)
                continue
            raise
    data = json.loads(response.text)
    return {
        "task_instructions": str(data.get("task_instructions", "")).strip(),
        "warnings": [str(w) for w in data.get("warnings", []) if w],
    }
