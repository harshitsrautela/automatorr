"""Shares several Gemini API keys: hands them out in turn, keeps each key under its per-minute request limit, and sets a key
aside while it is rate-limited or out of daily quota."""

import asyncio
import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# A key that hit the per-minute limit is skipped for this long
RATE_LIMIT_COOLDOWN_SECONDS = 45
# A key marked "out for today" is retried after at most this long, in case the daily-quota guess was wrong
_MAX_DAILY_HOLD_SECONDS = 8 * 3600
# Google's Gemini API starts a new daily quota at midnight Pacific time (12:30 pm IST in summer, 1:30 pm IST in winter), not
# at midnight UTC. The margin keeps a request sent right at the reset from being refused again.
_QUOTA_ZONE = ZoneInfo("America/Los_Angeles")
_RESET_MARGIN_SECONDS = 60

# The per-minute limit used when GEMINI_RPM is not set (or cannot be read)
_DEFAULT_RPM = 14


# Reads the per-minute limit from GEMINI_RPM's text: blank means the default, "20" or "14.5" give 20 or 14 (rounded down),
# a fraction between 0 and 1 gives 1 (the slowest pace there is, never "off"), and anything that is not a number logs a
# warning and gives the default, so a bad .env line never stops the server. Takes the text (or None when unset); returns the limit.
def _rpm_from_env(value: str | None) -> int:
    if value is None or not value.strip():
        return _DEFAULT_RPM
    try:
        number = float(value.strip())
        limit = int(number)
    except (ValueError, OverflowError):
        logger.warning("GEMINI_RPM=%r is not a number; using %d calls per minute", value, _DEFAULT_RPM)
        return _DEFAULT_RPM
    if 0 < number < 1:
        logger.warning("GEMINI_RPM=%r is below 1; using 1 call per minute (0 turns pacing off)", value)
        return 1
    return limit


# Calls allowed per key and model in any 60 seconds. Gemini's free tier allows 15 per minute per Google project and model;
# one less leaves room for clock differences. 0 or less turns pacing off (for paid keys with much higher limits).
RPM = _rpm_from_env(os.environ.get("GEMINI_RPM"))
# The length of the sliding window the per-minute limit is counted over
_WINDOW_SECONDS = 60.0
# A little extra on every pacing wait, so a call never lands a hair before the oldest one has left the window
_PACE_MARGIN_SECONDS = 0.05


# The model used only after the chosen model is out of daily quota on every key
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "gemini-3.1-flash-lite")


# The models to try in order: the chosen one, then the fallback (when it is a different model).
def models_for(primary: str) -> list[str]:
    return [primary] if not FALLBACK_MODEL or FALLBACK_MODEL == primary else [primary, FALLBACK_MODEL]


# Shows only the end of a key, so logs never contain the whole thing.
def mask(key: str) -> str:
    return f"…{key[-4:]}" if len(key) > 4 else "…"


# Seconds until the next daily quota reset (the next midnight in Pacific time, daylight saving included) plus a small margin.
# Takes an optional "now" for testing; both times are compared in UTC, because subtracting two times in the same zone
# ignores the clock change on the two days a year it happens.
def _seconds_to_quota_reset(now: datetime | None = None) -> float:
    now = (now or datetime.now(timezone.utc)).astimezone(_QUOTA_ZONE)
    reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (reset.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds() + _RESET_MARGIN_SECONDS


# Tracks which keys are usable for which model, and how many calls each one made in the last minute. One instance is shared
# by the whole server (see `pool`), across threads: every method takes the lock only briefly and never waits while holding it.
class KeyPool:
    # Takes an optional per-minute limit (default RPM), an optional wall clock in seconds for cool-downs and daily marks
    # (default time.time), and an optional pacing clock for the per-minute window (default time.monotonic, which no change
    # to the system clock moves; it pauses while the computer sleeps, which can only make a wait longer). When only `clock`
    # is given (tests), it runs both; tests can also set `clock` and `pace_clock` later.
    def __init__(self, rpm: int | None = None, clock: Callable[[], float] | None = None,
                 pace_clock: Callable[[], float] | None = None) -> None:
        self._lock = threading.Lock()
        self._blocked_until: dict[tuple[str, str], float] = {}  # (key, model) -> when usable again
        self._daily: set[tuple[str, str]] = set()  # (key, model) pairs set aside for the day
        self._recent: dict[tuple[str, str], deque[float]] = {}  # (key, model) -> pacing times of its newest calls, oldest first
        self._turn = 0
        self.rpm = RPM if rpm is None else rpm
        self.clock: Callable[[], float] = clock or time.time
        self.pace_clock: Callable[[], float] = pace_clock or clock or time.monotonic
        self.calls: dict[str, int] = {}  # key -> how many calls it was booked for

    # The pacing time to use: the one given (tests), else the pool's pacing clock.
    def _pace_now(self, now: float | None) -> float:
        return self.pace_clock() if now is None else now

    # The booked call times that still count against this key's minute at `now`, oldest first. A call stamped after `now`
    # counts too, so an earlier time never frees room. Reads only (booking is the one thing that changes the window).
    # Call with the lock held.
    def _in_window(self, key: str, model: str, now: float) -> list[float]:
        return [t for t in self._recent.get((key, model), ()) if t > now - _WINDOW_SECONDS]

    # Seconds until this key's window has room for one more call: 0 when it has room now. Call with the lock held.
    def _pace_wait(self, key: str, model: str, now: float) -> float:
        if self.rpm <= 0:
            return 0.0
        times = self._in_window(key, model, now)
        if len(times) < self.rpm:
            return 0.0
        # Room comes back when the call that is rpm-th from the end leaves the window (counted from now at the latest)
        return min(times[-self.rpm], now) + _WINDOW_SECONDS - now

    # The keys not set aside (cool-down or daily mark) right now. Call with the lock held.
    def _usable(self, keys: list[str], model: str, now: float) -> list[str]:
        return [k for k in keys if self._blocked_until.get((k, model), 0) <= now]

    # Returns the next usable key in turn, preferring keys whose last minute still has room for a call; when none has room,
    # the key whose window frees up soonest. Returns None when every key is set aside right now (cool-down or daily mark).
    # Takes the keys, the model name and an optional pacing time (for tests). Picking books nothing: `reserve` does.
    def pick(self, keys: list[str], model: str, now: float | None = None) -> str | None:
        with self._lock:
            usable = self._usable(keys, model, self.clock())
            if not usable:
                return None
            now = self._pace_now(now)
            waits = {k: self._pace_wait(k, model, now) for k in usable}
            with_room = [k for k in usable if waits[k] <= 0]
            if not with_room:
                return min(usable, key=lambda k: waits[k])
            key = with_room[self._turn % len(with_room)]
            self._turn += 1
            return key

    # Books one call on a key when its last minute has room: records the call and returns 0. Otherwise records nothing and
    # returns the seconds until the oldest call that counts leaves the window. Takes the key, the model and an optional
    # pacing time (for tests).
    def reserve(self, key: str, model: str, now: float | None = None) -> float:
        with self._lock:
            now = self._pace_now(now)  # read under the lock, so calls are booked in time order
            wait = self._pace_wait(key, model, now)
            if wait > 0:
                return wait
            if self.rpm > 0:
                times = self._recent.setdefault((key, model), deque())
                # Stamped no earlier than the newest call, so the times stay in order; only the newest rpm calls can
                # ever decide whether there is room, so older ones are dropped (whatever time a caller passes)
                times.append(max(now, times[-1]) if times else now)
                while len(times) > self.rpm:
                    times.popleft()
            self.calls[key] = self.calls.get(key, 0) + 1
            return 0.0

    # How many more calls this key may make for this model in the current minute (None when pacing is off). Changes
    # nothing. Takes the key, the model and an optional pacing time (for tests).
    def room(self, key: str, model: str, now: float | None = None) -> int | None:
        if self.rpm <= 0:
            return None
        with self._lock:
            return max(self.rpm - len(self._in_window(key, model, self._pace_now(now))), 0)

    # Seconds until some key can take a call for this model: one that is not set aside and has room in its minute. Counts
    # a key on a cool-down (or daily mark) from when it comes back, with its own minute's calls. Takes the keys, the model
    # and an optional pacing time (for tests); returns 0 when a key can take a call now, None when there are no keys.
    def ready_in(self, keys: list[str], model: str, now: float | None = None) -> float | None:
        with self._lock:
            wall, now = self.clock(), self._pace_now(now)
            waits = [max(self._blocked_until.get((k, model), 0) - wall, self._pace_wait(k, model, now), 0.0) for k in keys]
            return min(waits) if waits else None

    # Picks a key and books one call on it, keeping every key under its per-minute limit: when the picked key's minute is
    # full it tries the other usable keys, and only when none has room it sleeps (without holding the lock) until the
    # soonest key can take a call (a full key's minute frees up, or a cooled key comes back), then tries again. Takes the
    # keys and the model; returns the booked key, or None when every key is set aside (cool-down or daily mark), which the
    # caller handles as before.
    async def paced_pick(self, keys: list[str], model: str) -> str | None:
        while True:
            key = self.pick(keys, model)
            if key is None:
                return None
            if self.reserve(key, model) <= 0:
                return key
            # Another task may have taken the room between pick and reserve: any other usable key with room will do
            for other in keys:
                if other == key or self.wait_seconds([other], model) != 0:
                    continue  # the picked key itself, or one that is set aside
                if self.reserve(other, model) <= 0:
                    return other
            # A cooled key may come back before any full key's minute frees up, so wait for whichever is first
            wait = self.ready_in(keys, model) or 0.0
            logger.debug("Every usable key has made its %d calls this minute for %s; waiting %.1f s", self.rpm, model, wait)
            await asyncio.sleep(wait + _PACE_MARGIN_SECONDS)

    # Sets a key aside for a short while after a "too many requests" answer.
    def cool(self, key: str, model: str, seconds: float = RATE_LIMIT_COOLDOWN_SECONDS) -> None:
        with self._lock:
            until = self.clock() + seconds
            self._blocked_until[(key, model)] = max(self._blocked_until.get((key, model), 0), until)

    # Sets a key aside until its daily quota starts over.
    def exhaust_today(self, key: str, model: str) -> None:
        with self._lock:
            now = self.clock()
            self._daily.add((key, model))
            to_reset = _seconds_to_quota_reset(datetime.fromtimestamp(now, timezone.utc))
            self._blocked_until[(key, model)] = now + min(to_reset, _MAX_DAILY_HOLD_SECONDS)

    # How long until some key can be used: 0 if one is usable now, None if every key is out for the day.
    def wait_seconds(self, keys: list[str], model: str) -> float | None:
        now = self.clock()
        with self._lock:
            waits = []
            for k in keys:
                until = self._blocked_until.get((k, model), 0)
                if until <= now:
                    return 0.0
                if (k, model) not in self._daily:
                    waits.append(until - now)
            return min(waits) if waits else None

    # True when every key is out of its daily quota for this model.
    def all_exhausted(self, keys: list[str], model: str) -> bool:
        with self._lock:
            now = self.clock()
            return bool(keys) and all((k, model) in self._daily and self._blocked_until.get((k, model), 0) > now for k in keys)

    # The first model that still has quota on some key, or None when all are used up for the day.
    def first_open_model(self, keys: list[str], models: list[str]) -> str | None:
        return next((m for m in models if not self.all_exhausted(keys, m)), None)


pool = KeyPool()
