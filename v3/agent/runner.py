"""Runs records through the target form using a fresh browser-use Agent per record."""

import asyncio
import logging
import tempfile
from datetime import datetime, timezone
from typing import Callable

from browser_use import Agent, BrowserProfile
from browser_use.llm import ChatGoogle
from browser_use.llm.exceptions import ModelProviderError

from agent.prompts import build_task_prompt
from data.models import Record, RunResult
from keypool import mask, models_for, pool
from run_context import RunContext

logger = logging.getLogger(__name__)

# Daily quota error is a hard wall; the per-minute error is recoverable
_QUOTA_EXHAUSTED_SIGNATURE = "GenerateRequestsPerDayPerProjectPerModel"


# Phrases in an LLM error that mean the key or access is wrong, so retrying any record is pointless.
_AUTH_FAILURE_SIGNATURES = ("API key not valid", "API_KEY_INVALID", "PERMISSION_DENIED", "UNAUTHENTICATED")


# Checks whether the agent run failed because the LLM rejected the API key or access.
# Takes the agent run result and returns a plain-language message, or None if no such error.
def _detect_fatal_error(result) -> str | None:
    errors_fn = getattr(result, "errors", None)
    if errors_fn is None:
        return None
    try:
        step_errors = errors_fn()
    except Exception:
        return None
    for err in step_errors:
        if err and any(sig in err for sig in _AUTH_FAILURE_SIGNATURES):
            return "Gemini rejected the API key (or it has no access to this model). Check the key in Settings and the model name."
    return None


# Checks whether the agent run kept failing because Gemini said "too many requests" (HTTP 429, RESOURCE_EXHAUSTED).
# Takes the agent run result and returns a plain-language message, or None if no such error.
def _detect_rate_limit(result) -> str | None:
    errors_fn = getattr(result, "errors", None)
    if errors_fn is None:
        return None
    try:
        step_errors = errors_fn()
    except Exception:
        return None
    if any(err and "RESOURCE_EXHAUSTED" in err for err in step_errors):
        return (
            "Gemini rate limit reached (429: too many requests or quota used up for this key). "
            "Lower “Browsers at the same time” to 1, wait a minute, or use a key with higher limits."
        )
    return None


# Checks whether any step in an agent run failed with the Gemini daily quota error.
# Takes the agent run result and returns True if so, False if there are no errors or none match.
def _detect_quota_exhausted(result) -> bool:
    errors_fn = getattr(result, "errors", None)
    if errors_fn is None:
        return False
    try:
        step_errors = errors_fn()
    except Exception:
        return False
    return any(err and _QUOTA_EXHAUSTED_SIGNATURE in err for err in step_errors)


# The Gemini chat model for the browser agent, with several API keys. Every call uses the next usable key with room in its
# per-minute window (keypool.RPM), waiting when every key has made its share this minute; a key that answers "too many
# requests" or is out of daily quota is set aside and the same call goes to another key.
class PooledChatGoogle(ChatGoogle):
    _pool_keys: list[str]
    _primary_model: str

    # Makes the call on a key from the pool, switching keys instead of waiting whenever another one is free, and pacing calls
    # so no key goes over its per-minute limit. Takes the chat messages and optional output format; returns browser-use's answer.
    async def ainvoke(self, messages, output_format=None, **kwargs):
        keys = self._pool_keys
        models = models_for(self._primary_model)
        server_errors = 0
        for _attempt in range(14):
            model = pool.first_open_model(keys, models)
            if model is None:
                # Every key is out for the day on every model: raise an error that carries the daily-quota text the run recognises
                raise ModelProviderError(
                    message=f"429 RESOURCE_EXHAUSTED {_QUOTA_EXHAUSTED_SIGNATURE}: every key is out of daily quota",
                    status_code=429,
                    model=str(self.model),
                )
            if str(self.model) != model:
                logger.warning("Every key is out of daily quota for %s; using %s instead", self.model, model)
                self.model = model
            # Waits (outside any lock) when every usable key has already made its share of calls this minute
            key = await pool.paced_pick(keys, model)
            if key is None:
                wait = pool.wait_seconds(keys, model)
                await asyncio.sleep(min(wait if wait is not None else 5, 30) + 0.5)
                continue
            if key != self.api_key or self._client is None:
                self.api_key = key
                self._client = None
            try:
                return await super().ainvoke(messages, output_format, **kwargs)
            except ModelProviderError as exc:
                text = str(exc)
                if _QUOTA_EXHAUSTED_SIGNATURE in text:
                    pool.exhaust_today(key, model)
                    logger.warning("Gemini key %s is out of daily quota for %s; switching", mask(key), model)
                    continue
                if exc.status_code == 429 or "RESOURCE_EXHAUSTED" in text:
                    pool.cool(key, model)
                    logger.warning("Gemini key %s is rate-limited; switching keys", mask(key))
                    continue
                if exc.status_code in (500, 502, 503, 504) and server_errors < 3:
                    server_errors += 1
                    await asyncio.sleep(2**server_errors)
                    continue
                raise
        raise ModelProviderError(message="429 RESOURCE_EXHAUSTED: Gemini kept refusing on every key", status_code=429, model=str(self.model))


# Creates the Gemini chat model used by the browser agent.
# Takes the RunContext and returns a PooledChatGoogle that shares all of the run's keys.
def _build_llm(ctx: RunContext):
    keys = ctx.all_gemini_keys
    # One attempt per call: switching keys (above) replaces browser-use's own wait-and-retry on the same key
    llm = PooledChatGoogle(model=ctx.model_name, api_key=keys[0], max_retries=1)
    llm._pool_keys = keys
    llm._primary_model = ctx.model_name
    return llm


# Runs one task through a live browser-use Agent, in its own browser or a caller-provided one.
# Takes a Record, the RunContext, vision flag and optional profile, step callback and task text; returns a RunResult.
async def _run_single_record_async(
    record: Record,
    *,
    ctx: RunContext,
    use_vision: bool,
    browser_profile: BrowserProfile | None = None,
    register_new_step_callback: Callable | None = None,
    task_override: str | None = None,
) -> RunResult:
    task = task_override or build_task_prompt(record, ctx)

    # Runs the agent with the given browser profile and builds the outcome.
    # Takes a BrowserProfile and returns a RunResult with the self-reported success and quota flag.
    async def _run_with_profile(profile: BrowserProfile) -> RunResult:
        llm = _build_llm(ctx)
        agent = Agent(
            task=task,
            llm=llm,
            browser_profile=profile,
            use_vision=use_vision,
            register_new_step_callback=register_new_step_callback,
            # browser-use's own "judge" would ask the model once more at the end (with screenshots) for a verdict nothing
            # reads: the evaluator and the plan's own checks decide, so it is off
            use_judge=False,
        )
        result = await agent.run()

        # Self-reported success only; the evaluator is the source of truth
        self_reported_success = getattr(result, "is_successful", lambda: None)()
        quota_exhausted = _detect_quota_exhausted(result)
        fatal_error = None if quota_exhausted else _detect_fatal_error(result)
        # A rate-limit note belongs only to records that failed; a success that merely had to retry stays clean
        rate_limited = None if (quota_exhausted or fatal_error or self_reported_success) else _detect_rate_limit(result)
        try:
            summary = result.final_result()
        except Exception:
            summary = None

        return RunResult(
            record_id=record.record_id,
            success=bool(self_reported_success),
            error_message=fatal_error or ("Gemini daily quota exhausted" if quota_exhausted else rate_limited),
            timestamp=datetime.now(timezone.utc),
            quota_exhausted=quota_exhausted,
            fatal_error=fatal_error,
            summary=summary,
        )

    try:
        if browser_profile is not None:
            # Caller owns the browser lifecycle
            return await _run_with_profile(browser_profile)

        # Fresh temp profile gives an isolated Chrome window, closed after the record
        with tempfile.TemporaryDirectory(prefix="browser_use_profile_") as profile_dir:
            profile = BrowserProfile(user_data_dir=profile_dir, keep_alive=False)
            return await _run_with_profile(profile)

    except Exception as exc:  # noqa: BLE001 - one bad record must not crash the batch
        return RunResult(
            record_id=record.record_id,
            success=False,
            error_message=f"{type(exc).__name__}: {exc}",
            timestamp=datetime.now(timezone.utc),
        )
