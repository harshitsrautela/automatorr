"""
Wraps browser-use and runs records through the target form.

Each call to run_single_record spins up a fresh browser-use Agent — agent
state is never reused across records, so one record's run can't leak
context (or confusion) into the next.

Records run sequentially, one at a time — tried true concurrency (each
record in its own throwaway Chrome profile / user_data_dir, to avoid
browser-use's own documented "local Chrome is one shared browser; parallel
tasks fight over tabs and focus" problem) and the isolation itself worked,
but concurrent agents collectively exceeded the Gemini API's free-tier
quota (15 requests/min for gemini-3.5-flash-lite, shared across every
concurrent call) almost immediately, so every record failed with 429
RESOURCE_EXHAUSTED. A single record's own step pacing naturally stays
under that quota, which is why sequential works reliably. The per-record
isolated Chrome profile is kept even though we're sequential again — it's
a genuine improvement (no longer hijacks whatever Chrome you already have
open) and costs nothing extra since only one is ever alive at a time.
Revisit concurrency if/when the Gemini key moves off the free tier.

NOTE: browser-use's public API has shifted across versions. The import below
(browser_use.Agent + browser_use.llm.ChatGoogle) matches recent releases, but
if your installed version differs, check `python -c "import browser_use;
help(browser_use)"` and adjust the two import lines + Agent(llm=...) call —
everything else in this file (RunResult building, error handling, batching)
stays the same regardless of the exact browser-use version.
"""

import asyncio
import logging
import tempfile
import time
from datetime import datetime, timezone

from browser_use import Agent, BrowserProfile
from browser_use.llm import ChatGoogle

from agent.prompts import build_task_prompt
from config import GEMINI_API_KEY, MODEL_NAME
from data.models import Record, RunResult

logger = logging.getLogger(__name__)

# Gemini's free tier caps generate_content calls at 15/minute, shared across
# every call this process makes. A record that fails (rate limit, timeout,
# etc.) may have left that per-minute window mostly used up, so the next
# record would start already close to the ceiling. Pausing a full minute
# after a failure gives the window room to reset before trying again.
RATE_LIMIT_COOLDOWN_SECONDS = 60


def _build_llm():
    return ChatGoogle(model=MODEL_NAME, api_key=GEMINI_API_KEY)


async def _run_single_record_async(record: Record, *, use_vision: bool) -> RunResult:
    task = build_task_prompt(record)

    try:
        # Unique profile dir per record -> Chrome launches a brand-new,
        # isolated window for this agent instead of attaching to a shared
        # one. keep_alive=False closes that window once this record is done.
        with tempfile.TemporaryDirectory(prefix="browser_use_profile_") as profile_dir:
            llm = _build_llm()
            browser_profile = BrowserProfile(user_data_dir=profile_dir, keep_alive=False)
            agent = Agent(task=task, llm=llm, browser_profile=browser_profile, use_vision=use_vision)
            result = await agent.run()

            # browser-use reports its own view of success — we log it, but the
            # evaluator (reading Supabase directly) is the actual source of truth.
            self_reported_success = getattr(result, "is_successful", lambda: None)()

            return RunResult(
                record_id=record.record_id,
                success=bool(self_reported_success),
                error_message=None,
                timestamp=datetime.now(timezone.utc),
            )

    except Exception as exc:  # noqa: BLE001 - deliberately broad: one bad
        # record must not crash the whole batch (and must not crash while
        # siblings are still running concurrently in asyncio.gather)
        return RunResult(
            record_id=record.record_id,
            success=False,
            error_message=f"{type(exc).__name__}: {exc}",
            timestamp=datetime.now(timezone.utc),
        )


async def _run_single_record_with_vision_fallback_async(record: Record) -> RunResult:
    """Plan A: text-only, no screenshots — DataDesk's own Settings page
    advertises "semantic HTML, stable element IDs, accessible labels" for
    exactly this reason, so the DOM/element-list text browser-use already
    builds on every step should be enough on its own, at a fraction of the
    per-call cost of also sending an image.

    Plan B: only if plan A didn't succeed, retry the SAME record once with
    vision turned back on. Kept in its own function since we expect this to
    be rare for this form — this is a safety net, not the common path."""
    result = await _run_single_record_async(record, use_vision=False)
    if result.success:
        return result

    logger.warning(
        "👁️ Record %s didn't complete on the text-only (no-vision) pass — "
        "retrying once with vision enabled as a fallback.",
        record.record_id,
    )
    # Give the per-minute Gemini quota a breather before immediately firing
    # another full run of calls at it — a text-only failure is often the
    # same rate limit that would just as quickly reject the vision retry.
    # asyncio.sleep, NOT time.sleep: this coroutine can be one of several
    # running concurrently under asyncio.gather (run_batch_concurrent) —
    # a blocking time.sleep would freeze the whole shared event loop and
    # stall every other record's agent for the full cooldown, not just
    # this one's.
    await asyncio.sleep(RATE_LIMIT_COOLDOWN_SECONDS)

    return await _run_single_record_async(record, use_vision=True)


def run_single_record(record: Record) -> RunResult:
    """Sync wrapper — browser-use's Agent.run() is async under the hood."""
    return asyncio.run(_run_single_record_with_vision_fallback_async(record))


def run_batch(records: list[Record]) -> list[RunResult]:
    """v1 — the default/safe mode. Runs every record sequentially, one at a
    time (see module docstring for why: Gemini free-tier rate limit, not a
    browser bug). This is what api/routes.py calls when
    config.CONCURRENT_MODE is false.

    If a record comes back unsuccessful, pauses before the next one instead
    of immediately hammering the same per-minute Gemini quota again — logged
    clearly so it reads as a deliberate cooldown, not a hang."""
    results: list[RunResult] = []
    for i, record in enumerate(records):
        result = run_single_record(record)
        results.append(result)

        is_last_record = i == len(records) - 1
        if not result.success and not is_last_record:
            logger.warning(
                "⏳ Record %s did not complete successfully — pausing %ss "
                "before the next record so Gemini's free-tier per-minute "
                "quota has room to reset. This is an intentional cooldown, "
                "not a crash or hang.",
                record.record_id,
                RATE_LIMIT_COOLDOWN_SECONDS,
            )
            time.sleep(RATE_LIMIT_COOLDOWN_SECONDS)
            logger.info("▶️ Cooldown finished, resuming batch with the next record.")

    return results


async def _run_batch_concurrent_async(records: list[Record]) -> list[RunResult]:
    """Kicks off every record's browser-use Agent at once, each in its own
    isolated Chrome profile (see _run_single_record_async) so concurrent
    agents never share a tab, a focus pointer, or any browser state.
    _run_single_record_with_vision_fallback_async already catches every
    exception internally and returns a failed RunResult instead of raising,
    so one record blowing up (e.g. hitting the shared Gemini rate limit)
    can't take down asyncio.gather or the records running alongside it."""
    return list(
        await asyncio.gather(
            *(_run_single_record_with_vision_fallback_async(r) for r in records)
        )
    )


def run_batch_concurrent(records: list[Record]) -> list[RunResult]:
    """v2 — every record at once, each in its own isolated browser window.
    Only worth using when there's real headroom on the Gemini quota: the
    free tier's 15-requests/minute cap is exactly what made this fail 100%
    of the time the first time it was tried (see LEARNINGS.md) — concurrent
    agents share that quota, so more browser windows does not mean more
    API-request capacity. This function is kept separate from run_batch
    (v1, sequential) rather than replacing it, specifically so switching
    back is a one-line change: flip config.CONCURRENT_MODE to false (or
    just call run_batch directly) and nothing else needs to change."""
    return asyncio.run(_run_batch_concurrent_async(records))
