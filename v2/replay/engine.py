"""Replays a cached step on a fresh page with plain async Playwright, no LLM call."""

import logging
from dataclasses import dataclass

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from cache.fingerprint import compute_fingerprint
from cache.store import CachedStep

logger = logging.getLogger(__name__)

_ATTRIBUTE_PRIORITY = ("data-testid", "data-test", "data-cy", "id", "name", "aria-label")

# Outcome of a replay attempt, with a failure reason when it did not succeed.
# Holds ok plus an optional reason string such as "no_match" or "execution_failed".
@dataclass
class ReplayResult:
    ok: bool
    reason: str | None = None  # set when ok is False: "no_match" | "execution_failed" | "field_missing_in_record"


# Performs cached click, input, select and checkbox actions on a Playwright page.
# Locates each element from its cached xpath or attributes, with no LLM involved.
class ReplayEngine:
    # Stores the page to act on and the per-action timeout.
    # Takes a Playwright Page and an optional timeout in milliseconds (default 5000).
    def __init__(self, page: Page, *, timeout_ms: int = 5000):
        self.page = page
        self.timeout_ms = timeout_ms

    # Finds the cached element by xpath, then by attribute selector.
    # Takes a CachedStep and returns a Locator, or None if nothing matches exactly once.
    async def _locate(self, step: CachedStep) -> Locator | None:
        # Tier 1: xpath
        candidate = self.page.locator(f"xpath={step.x_path}")
        try:
            count = await candidate.count()
        except Exception:
            count = 0
        if count == 1:
            await self._check_fingerprint(candidate, step)
            return candidate
        if count > 1:
            logger.warning(
                "xpath matched %d elements for step %s.%s — ambiguous, trying attribute selector instead.",
                count,
                step.step_index,
                step.action_offset,
            )

        # Tier 2: attribute selector
        selector = self._attribute_selector(step.attributes)
        if selector:
            candidate = self.page.locator(selector)
            try:
                count = await candidate.count()
            except Exception:
                count = 0
            if count == 1:
                await self._check_fingerprint(candidate, step)
                return candidate
            if count > 1:
                logger.warning(
                    "attribute selector %r matched %d elements for step %s.%s — ambiguous.",
                    selector,
                    count,
                    step.step_index,
                    step.action_offset,
                )

        return None

    # Builds a CSS attribute selector from the highest-priority attribute present.
    # Takes the cached attributes dict and returns a selector string, or None if none is usable.
    def _attribute_selector(self, attributes: dict[str, str]) -> str | None:
        for attr in _ATTRIBUTE_PRIORITY:
            value = attributes.get(attr)
            if value:
                escaped = value.replace('"', '\\"')
                return f'[{attr}="{escaped}"]'
        return None

    # Logs a warning if the located element's fingerprint differs from the cached one.
    # Takes a Locator and a CachedStep; never blocks the replay and returns nothing.
    async def _check_fingerprint(self, locator: Locator, step: CachedStep) -> None:
        try:
            info = await locator.evaluate(
                "el => ({tag: el.tagName, attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value]))})"
            )
        except Exception:
            return
        recomputed = compute_fingerprint(info.get("tag", ""), info.get("attrs", {}))
        if recomputed != step.fingerprint:
            logger.warning(
                "Fingerprint mismatch for step %s.%s (field=%s) — element found by locator no longer looks "
                "like what was cached. Proceeding anyway since the locator itself matched uniquely; this is a "
                "signal worth watching in cache hit-rate trends, not a hard stop.",
                step.step_index,
                step.action_offset,
                step.field_name,
            )

    # Locates the cached element and performs the action its action_type calls for.
    # Takes a CachedStep and the value to enter, and returns a ReplayResult saying whether it worked.
    async def perform(self, step: CachedStep, value: object | None) -> ReplayResult:
        locator = await self._locate(step)
        if locator is None:
            return ReplayResult(ok=False, reason="no_match")

        try:
            # Scroll first; cached elements may be below the fold
            await locator.scroll_into_view_if_needed(timeout=self.timeout_ms)
            if step.action_type == "click":
                await locator.click(timeout=self.timeout_ms)
            elif step.action_type == "input":
                await locator.fill("" if value is None else str(value), timeout=self.timeout_ms)
            elif step.action_type == "select_dropdown":
                await locator.select_option(label=str(value), timeout=self.timeout_ms)
            elif step.action_type == "checkbox":
                # Only click if the checkbox state differs from the desired value
                desired = bool(value)
                is_checked = await locator.is_checked()
                if is_checked != desired:
                    await locator.click(timeout=self.timeout_ms)
            else:
                return ReplayResult(ok=False, reason="no_match")
            return ReplayResult(ok=True)
        except PlaywrightTimeoutError:
            logger.warning("Timed out performing %s on step %s.%s", step.action_type, step.step_index, step.action_offset)
            return ReplayResult(ok=False, reason="execution_failed")
        except Exception:
            logger.exception("Replay execution failed on step %s.%s", step.step_index, step.action_offset)
            return ReplayResult(ok=False, reason="execution_failed")
