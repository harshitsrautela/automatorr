"""Offline checks for pacing model calls under Gemini's per-minute limit (no network, no browser, no real waiting):

    python tests/test_pacing.py

The key pool runs on a fake clock, and asyncio.sleep is swapped for one that moves that clock forward, so minutes of waiting
take a fraction of a second. The model client (genai.Client) and browser-use's chat call are fakes that note when they were
called, so the checks can count calls per minute."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")

import asyncio
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import keypool
from keypool import RATE_LIMIT_COOLDOWN_SECONDS, KeyPool
from run_context import RunContext

checks = 0


# Prints "ok   name" or "FAIL name" and stops the script on the first failure. Takes the check's name and whether it held.
def check(name, ok):
    global checks
    checks += 1
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        sys.exit(1)


# A clock the test moves by hand: calling it returns the current fake time in seconds.
class FakeClock:
    # Takes the starting time in seconds.
    def __init__(self, start: float) -> None:
        self.t = start

    # Returns the current fake time.
    def __call__(self) -> float:
        return self.t


# The largest number of calls that fall inside any 60-second stretch. Takes the call times; returns the count.
def busiest_minute(times: list[float]) -> int:
    times = sorted(times)
    return max((sum(1 for u in times[i:] if u < t + 60) for i, t in enumerate(times)), default=0)


# Swaps asyncio.sleep for one that moves the fake clock forward instead of waiting (the clock only ever moves forward, so a
# sleeper that wakes after a longer one is simply late, never early). Takes the clock; yields the list of requested waits.
@contextmanager
def fake_sleep(clock: FakeClock):
    real_sleep = asyncio.sleep
    waits: list[float] = []

    # Notes the wait, lets other tasks run once, then moves the clock to the wake-up time. Takes asyncio.sleep's arguments.
    async def sleep(seconds, result=None):
        target = clock.t + max(seconds, 0)
        waits.append(seconds)
        await real_sleep(0)
        clock.t = max(clock.t, target)
        return result

    asyncio.sleep = sleep
    try:
        yield waits
    finally:
        asyncio.sleep = real_sleep


T0 = datetime(2026, 10, 9, 4, 0, tzinfo=timezone.utc).timestamp()

# ------------------------------------------------------------------ the window itself

result = subprocess.run([sys.executable, "-c", "import keypool; print(keypool.RPM, keypool.pool.rpm)"], capture_output=True, text=True,
                        env={**os.environ, "GEMINI_RPM": "7"}, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
check("GEMINI_RPM sets the per-minute limit (14 when unset)", result.stdout.split() == ["7", "7"] and (keypool.RPM == 14 or "GEMINI_RPM" in os.environ))
imports = [subprocess.run([sys.executable, "-c", "import keypool; print(keypool.RPM)"], capture_output=True, text=True,
                          env={**os.environ, "GEMINI_RPM": value}, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
           for value in ("", "abc", "14.5")]
check("a blank or bad GEMINI_RPM no longer stops the import (it falls back to 14)", [(r.returncode, r.stdout.strip()) for r in imports] == [(0, "14")] * 3)
keypool.logger.disabled = True  # the bad values below log an expected warning each
parsed = [keypool._rpm_from_env(v) for v in (None, "", "  ", " 20 ", "14.5", "-1", "0", "abc", "inf", "nan")]
fractions = [keypool._rpm_from_env(v) for v in ("0.5", "0.9", "1.5", "-0.5")]
keypool.logger.disabled = False
check("GEMINI_RPM text is read forgivingly", parsed == [14, 14, 14, 20, 14, -1, 0, 14, 14, 14])
half = subprocess.run([sys.executable, "-c", "import keypool; p = keypool.KeyPool(); print(keypool.RPM, sum(p.reserve('k', 'm') == 0 for _ in range(30)))"],
                      capture_output=True, text=True, env={**os.environ, "GEMINI_RPM": "0.5"}, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
check("a GEMINI_RPM between 0 and 1 paces at 1 a minute instead of turning pacing off",
      fractions == [1, 1, 1, 0] and half.stdout.split() == ["1", "1"] and "below 1" in half.stderr)

clock = FakeClock(T0)
p = KeyPool(rpm=14, clock=clock)
first = [p.reserve("k1", "m") for _ in range(14)]
check("the first 14 calls in a minute are booked at once", first == [0.0] * 14 and p.room("k1", "m") == 0 and p.calls["k1"] == 14)
clock.t += 20
wait = p.reserve("k1", "m")
check("the 15th call waits until the oldest call leaves the window", abs(wait - 40) < 1e-9)
check("a refused reservation records nothing", p.reserve("k1", "m") == wait and p.calls["k1"] == 14 and p.room("k1", "m") == 0)
check("each model has its own window on the same key", p.reserve("k1", "other-model") == 0.0 and p.room("k1", "m") == 0)
clock.t += 40
check("exactly 60 s later the minute has room again", p.reserve("k1", "m") == 0.0 and p.room("k1", "m") == 13)

clock = FakeClock(T0)
p = KeyPool(rpm=3, clock=clock)
for gap in (0, 10, 10):
    clock.t += gap
    p.reserve("k1", "m")
clock.t += 5  # calls at 0, 10, 20; now 25
w1 = p.reserve("k1", "m")
clock.t = T0 + 60
w2 = p.reserve("k1", "m")
w3 = p.reserve("k1", "m")
check("the window slides: one call leaves at a time", abs(w1 - 35) < 1e-9 and w2 == 0.0 and abs(w3 - 10) < 1e-9)
check("an explicit time can be passed instead of the clock", p.reserve("k1", "m", now=T0 + 200) == 0.0 and p.room("k1", "m", now=T0 + 200) == 2)

off = KeyPool(rpm=0, clock=FakeClock(T0))
check("a limit of 0 turns pacing off", all(off.reserve("k1", "m") == 0.0 for _ in range(100)) and off.room("k1", "m") is None)

fresh = KeyPool()
check("by default the minute is counted on time.monotonic, cool-downs on time.time", fresh.pace_clock is time.monotonic and fresh.clock is time.time)

clock = FakeClock(T0)
p = KeyPool(rpm=14, clock=clock)
for _ in range(14):
    p.reserve("k1", "m")
asked = (p.room("k1", "m", now=T0 - 100), p.pick(["k1"], "m", now=T0 - 100), p.ready_in(["k1"], "m", now=T0 - 100), p.reserve("k1", "m", now=T0 - 100))
clock.t = T0 + 1
check("asking with an earlier time changes nothing: the minute is still full 1 s later",
      asked == (0, "k1", 60.0, 60.0) and abs(p.reserve("k1", "m") - 59) < 1e-9 and p.room("k1", "m") == 0 and p.calls["k1"] == 14)

clock = FakeClock(T0)
p = KeyPool(rpm=14, clock=clock)
for _ in range(14):
    p.reserve("k1", "m")
clock.t = T0 - 99  # a single clock set back...
back = p.reserve("k1", "m")
clock.t = T0 + 2  # ...and put right again
check("a clock set back and put right lets no extra call out", abs(back - 60) < 1e-9 and abs(p.reserve("k1", "m") - 58) < 1e-9 and p.room("k1", "m") == 0)

wall, steady = FakeClock(T0), FakeClock(1000.0)
p = KeyPool(rpm=14, clock=wall, pace_clock=steady)
for _ in range(14):
    p.reserve("k1", "m")
steady.t += 10
wall.t -= 99  # the system clock is set back...
behind = p.reserve("k1", "m")
wall.t += 99 + 61  # ...then jumps a minute ahead
ahead = p.reserve("k1", "m")
check("the minute is counted on the steady clock: system clock steps either way let no extra call out",
      abs(behind - 50) < 1e-9 and abs(ahead - 50) < 1e-9 and p.room("k1", "m") == 0 and p.calls["k1"] == 14)
p.cool("k2", "m")
steady.t += 50
still_cooling = p.pick(["k2"], "m") is None
wall.t += RATE_LIMIT_COOLDOWN_SECONDS + 1
check("cool-downs still run on the system clock, and the minute frees up on the steady one",
      still_cooling and p.pick(["k2"], "m") == "k2" and p.reserve("k1", "m") == 0.0 and p.room("k1", "m") == 13)

p = KeyPool(rpm=2, clock=FakeClock(T0))
p.reserve("k1", "m", now=T0)
p.reserve("k1", "m", now=T0 + 30)
slightly_old = p.reserve("k1", "m", now=T0 + 29.5)
check("a time a little older than a booked call (threads, tests) changes nothing",
      abs(slightly_old - 30.5) < 1e-9 and p.reserve("k1", "m", now=T0 + 60) == 0.0 and p.reserve("k1", "m", now=T0 + 89.9) > 0)

# ------------------------------------------------------------------ several keys sharing the load

clock = FakeClock(T0)
p = KeyPool(rpm=14, clock=clock)
keys = ["k1", "k2"]
check("keys are still handed out in turn while both have room", {p.pick(keys, "m"), p.pick(keys, "m")} == {"k1", "k2"})
for _ in range(14):
    p.reserve("k2", "m")
check("pick prefers a key that still has room this minute", all(p.pick(keys, "m") == "k1" for _ in range(6)))
clock.t += 10
for _ in range(14):
    p.reserve("k1", "m")
clock.t += 20
check("with no room anywhere, pick returns the key that frees up first", p.pick(keys, "m") == "k2" and abs(p.reserve("k2", "m") - 30) < 1e-9)


# Books `count` calls one after another through paced_pick on a fresh pool. Takes the keys, the count and the limit; returns
# (the booked (time, key) pairs, the fake seconds it took, the waits asked for).
def paced_burst(keys: list[str], count: int, rpm: int = 14) -> tuple[list[tuple[float, str]], float, list[float]]:
    clock = FakeClock(T0)
    pool = KeyPool(rpm=rpm, clock=clock)
    booked: list[tuple[float, str]] = []

    # Books the calls one after another, noting when and on which key each one went out.
    async def run() -> None:
        for _ in range(count):
            key = await pool.paced_pick(keys, "m")
            booked.append((clock.t, key))

    with fake_sleep(clock) as waits:
        asyncio.run(run())
    return booked, clock.t - T0, waits


booked, took, waits = paced_burst(["k1"], 40)
check("a burst of 40 calls on 1 key at 14 per minute takes at least 2 minutes", took >= 120 and len(booked) == 40)
check("and no minute of that burst has more than 14 calls", busiest_minute([t for t, _ in booked]) == 14)
check("it waits in a few long sleeps, not a busy loop", len(waits) == 2 and all(w >= 59 for w in waits))

booked, took, waits = paced_burst(["k1", "k2"], 28)
check("two keys share the load: 28 calls go out at once, 14 on each", took == 0 and not waits and sorted(k for _, k in booked).count("k1") == 14)
booked, took, waits = paced_burst(["k1", "k2"], 40)
check("40 calls on two keys need one wait instead of two", 60 <= took < 120 and all(busiest_minute([t for t, k in booked if k == key]) <= 14 for key in ("k1", "k2")))

# ------------------------------------------------------------------ cool-downs and daily marks still apply

clock = FakeClock(T0)
p = KeyPool(rpm=14, clock=clock)
p.cool("k1", "m")
check("a cooled key with room is still skipped", all(p.pick(keys, "m") == "k2" for _ in range(4)))
with fake_sleep(clock):
    got = asyncio.run(p.paced_pick(keys, "m"))
check("paced picking skips a cooled key too", got == "k2" and p.room("k1", "m") == 14)
clock.t += RATE_LIMIT_COOLDOWN_SECONDS + 1
check("a cooled key comes back after its cool-down on the pool's clock", "k1" in {p.pick(keys, "m"), p.pick(keys, "m")})
p.exhaust_today("k2", "m")
p.cool("k1", "m")
with fake_sleep(clock):
    got = asyncio.run(p.paced_pick(keys, "m"))
check("paced picking returns None when every key is set aside", got is None and p.wait_seconds(keys, "m") > 0)
p.exhaust_today("k1", "m")
check("every key out for the day is still recognised", p.all_exhausted(keys, "m") and p.wait_seconds(keys, "m") is None)

margin = keypool._PACE_MARGIN_SECONDS + 1e-6
clock = FakeClock(T0)
p = KeyPool(rpm=14, clock=clock)
for _ in range(14):
    p.reserve("k1", "m")
clock.t += 1
p.cool("k2", "m")  # k2 answered 429: back at T0 + 46 with an empty minute, while k1's minute frees at T0 + 60
check("ready_in counts a cooled key from when it comes back", abs(p.ready_in(keys, "m") - 45) < 1e-9 and abs(p.ready_in(["k1"], "m") - 59) < 1e-9 and p.ready_in([], "m") is None)
with fake_sleep(clock) as waits:
    got = asyncio.run(p.paced_pick(keys, "m"))
check("a full key and a cooled key: the call goes out when the cooled key comes back, not 14 s later", got == "k2" and 0 <= clock.t - (T0 + 46) <= margin and len(waits) == 1)

clock = FakeClock(T0)
p = KeyPool(rpm=2, clock=clock)
p.reserve("k1", "m")
clock.t = T0 + 30
p.reserve("k1", "m")
p.cool("k2", "m", 5)
with fake_sleep(clock):
    got = asyncio.run(p.paced_pick(keys, "m"))
check("a short cool-down beats a full minute (booked at 35 s, not 60 s)", got == "k2" and 0 <= clock.t - (T0 + 35) <= margin)

clock = FakeClock(T0)
p = KeyPool(rpm=2, clock=clock)
p.reserve("k2", "m")
p.reserve("k2", "m")  # k2's minute frees at T0 + 60
clock.t = T0 + 5
p.reserve("k1", "m")
p.reserve("k1", "m")  # k1's minute frees at T0 + 65
p.cool("k2", "m", 10)  # k2 is back at T0 + 15, but its minute is still full then
with fake_sleep(clock) as waits:
    got = asyncio.run(p.paced_pick(keys, "m"))
check("a cooled key whose own minute is full is counted from when both have passed", got == "k2" and 0 <= clock.t - (T0 + 60) <= margin and len(waits) == 1)

clock = FakeClock(datetime(2026, 10, 9, 6, 15, tzinfo=timezone.utc).timestamp())  # 23:15 on 8 Oct in Pacific time
p = KeyPool(rpm=14, clock=clock)
p.exhaust_today("k1", "m")
clock.t += 44 * 60
held = p.pick(["k1"], "m") is None
clock.t += 2 * 60
check("a daily mark lasts until just after midnight Pacific, on the pool's clock", held and p.pick(["k1"], "m") == "k1" and not p.all_exhausted(["k1"], "m"))

# ------------------------------------------------------------------ thread safety

clock = FakeClock(T0)
p = KeyPool(rpm=14, clock=clock)
results: list[float] = []
lock = threading.Lock()


# One thread's share: tries to book 50 calls on the same key at once and adds the answers to `results`.
def hammer() -> None:
    mine = [p.reserve("k1", "m") for _ in range(50)]
    with lock:
        results.extend(mine)


threads = [threading.Thread(target=hammer) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("8 threads booking 400 calls at once get exactly 14", results.count(0.0) == 14 and p.calls["k1"] == 14 and len(results) == 400)

# ------------------------------------------------------------------ the text-model call (recipe/llm.py)

import recipe.llm as llm

sent: list[tuple[float, str, str]] = []
refuse_once: set[str] = set()


# Stands in for genai.Client: generate_content notes (fake time, key, model) and answers a small JSON object, or answers
# "429" once for a key listed in refuse_once.
class FakeClient:
    # Takes the API key, like genai.Client, and offers client.aio.models.generate_content.
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self.generate_content))

    # Notes the call and returns a fake response with a small JSON text (or raises a 429 once for a key in refuse_once).
    async def generate_content(self, model, contents, config):
        sent.append((llm_clock.t, self.api_key, model))
        if self.api_key in refuse_once:
            refuse_once.discard(self.api_key)
            raise RuntimeError("429 RESOURCE_EXHAUSTED: per-minute limit")
        return SimpleNamespace(text='{"ok": true}')


# Runs `count` ask_json calls at the same time on a fresh paced pool. Takes the keys and the count; returns the answers.
def ask_many(keys: list[str], count: int) -> list[dict]:
    global llm_clock
    llm_clock = FakeClock(T0)
    sent.clear()
    llm.pool = KeyPool(rpm=14, clock=llm_clock)
    ctx = RunContext(target_url="x", workflow_name="w", match_key_field="id", model_name="m", gemini_api_key=keys[0],
                     extra_gemini_keys=tuple(keys[1:]), task_instructions="rules")

    # Sends all the questions at the same time and returns their answers in order.
    async def run() -> list[dict]:
        return await asyncio.gather(*(llm.ask_json(ctx, f"question {n}") for n in range(count)))

    with fake_sleep(llm_clock):
        return asyncio.run(run())


real_pool, real_client = llm.pool, llm.genai.Client
llm.genai.Client = FakeClient
try:
    answers = ask_many(["k1"], 30)
    times = [t for t, _, _ in sent]
    check("ask_json: 30 calls at once on one key all get their answer", answers == [{"ok": True}] * 30 and len(sent) == 30)
    check("ask_json: never more than 14 calls in any minute", busiest_minute(times) == 14)
    check("ask_json: 30 calls at 14 per minute spread over at least 2 minutes", max(times) - T0 >= 120)

    answers = ask_many(["k1", "k2"], 28)
    check("ask_json: two keys take 14 calls each without waiting", all(t == T0 for t, _, _ in sent) and [k for _, k, _ in sent].count("k2") == 14)

    refuse_once.add("k1")
    answers = ask_many(["k1", "k2"], 3)
    check("ask_json: a 429 still cools the key and the call goes to the other key", answers == [{"ok": True}] * 3 and llm.pool.pick(["k1", "k2"], "m") == "k2" and len(sent) == 4)
finally:
    llm.pool, llm.genai.Client = real_pool, real_client

# ------------------------------------------------------------------ the browser agent's chat model (agent/runner.py)

import agent.runner as runner
from browser_use.llm import ChatGoogle

agent_calls: list[tuple[float, str]] = []


# Stands in for browser-use's own ChatGoogle.ainvoke: notes (fake time, key) and answers.
async def fake_ainvoke(self, messages, output_format=None, **kwargs):
    agent_calls.append((agent_clock.t, self.api_key))
    return "done"


agent_clock = FakeClock(T0)
real_runner_pool, real_ainvoke = runner.pool, ChatGoogle.ainvoke
runner.pool = KeyPool(rpm=14, clock=agent_clock)
ChatGoogle.ainvoke = fake_ainvoke
try:
    ctx = RunContext(target_url="x", workflow_name="w", match_key_field="id", model_name="m", gemini_api_key="k1", task_instructions="rules")

    # Sends 20 agent steps at the same time through the pooled chat model; returns their replies.
    async def agent_burst() -> list:
        return await asyncio.gather(*(runner._build_llm(ctx).ainvoke([]) for _ in range(20)))

    with fake_sleep(agent_clock):
        replies = asyncio.run(agent_burst())
    check("browser agent: 20 steps on one key keep to 14 per minute", replies == ["done"] * 20 and busiest_minute([t for t, _ in agent_calls]) == 14 and max(t for t, _ in agent_calls) - T0 >= 60)
finally:
    runner.pool, ChatGoogle.ainvoke = real_runner_pool, real_ainvoke

# ------------------------------------------------------------------ 15 Oct review: the agent's unused "judge" call is off

agent_kwargs: dict = {}


# Stands in for browser-use's Agent: notes how it was built and finishes at once.
class FakeAgent:
    def __init__(self, **kwargs) -> None:
        agent_kwargs.update(kwargs)

    async def run(self):
        return SimpleNamespace(is_successful=lambda: True, final_result=lambda: "done", history=[], errors=lambda: [])


real_agent = runner.Agent
runner.Agent = FakeAgent
try:
    from data.models import Record
    asyncio.run(runner._run_single_record_async(Record(record_id="R1"), ctx=RunContext(target_url="x", workflow_name="w", match_key_field="id", model_name="m",
                                                                                     gemini_api_key="k1", task_instructions="rules"),
                                                use_vision=False, browser_profile=SimpleNamespace()))
finally:
    runner.Agent = real_agent
check("the browser agent is built without browser-use's judge (one more model call per run, with screenshots, that nothing reads)",
      agent_kwargs.get("use_judge") is False)

# ------------------------------------------------------------------ 15 Oct review: writing instructions is paced in the same pool

import prompt_gen

gen_calls: list[tuple[float, str]] = []


# Stands in for genai.Client in prompt_gen: notes (fake time, key) and answers the instructions as JSON.
class FakeSyncClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.models = SimpleNamespace(generate_content=self.generate_content)

    def generate_content(self, model, contents, config):
        gen_calls.append((gen_clock.t, self.api_key))
        return SimpleNamespace(text='{"task_instructions": "Do it.", "warnings": []}')


gen_clock = FakeClock(T0)
real_gen_pool, real_gen_client, real_gen_sleep = prompt_gen.pool, prompt_gen.genai.Client, prompt_gen.time.sleep
prompt_gen.pool = KeyPool(rpm=2, clock=gen_clock)
prompt_gen.genai.Client = FakeSyncClient
prompt_gen.time = SimpleNamespace(sleep=lambda s: setattr(gen_clock, "t", gen_clock.t + s))
try:
    for _ in range(2):
        prompt_gen.pool.reserve("k1", "m")  # a running batch has used this minute's calls
    got = prompt_gen.generate_instructions("approve invoices", "http://x", {}, "m", "k1")
    check("writing instructions books its call in the key pool and waits for room in the key's minute (a batch's calls are not crowded out)",
          got["task_instructions"] == "Do it." and len(gen_calls) == 1 and gen_calls[0][0] >= T0 + 60 and prompt_gen.pool.calls["k1"] == 3)
finally:
    prompt_gen.pool, prompt_gen.genai.Client, prompt_gen.time = real_gen_pool, real_gen_client, time

print(f"\n{checks} checks passed")
