"""Runs records through the target form using a fresh browser-use Agent per record."""

import tempfile
from datetime import datetime, timezone
from typing import Callable

from browser_use import Agent, BrowserProfile
from browser_use.llm import ChatGoogle

from agent.prompts import build_task_prompt
from config import GEMINI_API_KEY, MODEL_NAME
from data.models import Record, RunResult

# Daily quota error is a hard wall; the per-minute error is recoverable
_QUOTA_EXHAUSTED_SIGNATURE = "GenerateRequestsPerDayPerProjectPerModel"


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


# Creates the Gemini chat model used by the browser agent.
# Takes no arguments and returns a ChatGoogle configured with MODEL_NAME and GEMINI_API_KEY.
def _build_llm():
    return ChatGoogle(model=MODEL_NAME, api_key=GEMINI_API_KEY)


# Runs one task through a live browser-use Agent, in its own browser or a caller-provided one.
# Takes a Record, vision flag and optional profile, step callback and task text; returns a RunResult.
async def _run_single_record_async(
    record: Record,
    *,
    use_vision: bool,
    browser_profile: BrowserProfile | None = None,
    register_new_step_callback: Callable | None = None,
    task_override: str | None = None,
) -> RunResult:
    task = task_override or build_task_prompt(record)

    # Runs the agent with the given browser profile and builds the outcome.
    # Takes a BrowserProfile and returns a RunResult with the self-reported success and quota flag.
    async def _run_with_profile(profile: BrowserProfile) -> RunResult:
        llm = _build_llm()
        agent = Agent(
            task=task,
            llm=llm,
            browser_profile=profile,
            use_vision=use_vision,
            register_new_step_callback=register_new_step_callback,
        )
        result = await agent.run()

        # Self-reported success only; the evaluator is the source of truth
        self_reported_success = getattr(result, "is_successful", lambda: None)()
        quota_exhausted = _detect_quota_exhausted(result)

        return RunResult(
            record_id=record.record_id,
            success=bool(self_reported_success),
            error_message="Gemini daily quota exhausted" if quota_exhausted else None,
            timestamp=datetime.now(timezone.utc),
            quota_exhausted=quota_exhausted,
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
