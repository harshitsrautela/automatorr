# Tool comparison on the AP Desk, 13 Oct 2026

Same task text for every tool (`tasks/*.txt`), the same 5 invoices (one of each decision kind: short receipt, price
variance, approve, vendor blocked, no open PO), the same model (`gemini-3.5-flash-lite`), headless, one tool at a time, the
AP Desk reset before each tool, graded by `grade.py` against the site's `expected.json`. Every model call went through
`proxy.js`, which counts them the same way for every tool (`logs/calls.jsonl`, `python3 calls.py`).

## Result

| Tool | Correct | Model calls | Time | Reusable for later records? |
|---|---|---|---|---|
| **v3** (terminautomator, run #69, saved task 37) | **4 / 5** | **78** | **7 min 43 s** | Learned a plan after record 1 and it agreed with the live agent on record 2. On record 3 it was **thrown away**: the live agent had not filled the payment date, so the plan (which had the right date) "disagreed" with it. Records 3-5 ran live. |
| **Stagehand 3.7.3** (agent, dom mode, cache with `%invoice_no%` variable) | **0 / 5** | 56 (record 1 only) | 6 min 49 s for record 1, then a crash | Record 1 took 41 steps, never reached Goods receipts or the decision form, and reported **"Task completed successfully"** with nothing saved. Its cache is a blind replay of record 1's clicks (incl. a `javascript:alert(localStorage)` address it had typed); replaying it on record 2 killed the browser connection and ended the run. |
| **Skyvern 1.0.55** (task block, run_with=code, AI fallback, SQLite, no Docker) | **0 / 5** | 368 (4 records + part of the 5th, then stopped by the 350-call budget) | 30 min for 4 records | Each record hit Skyvern's 40-step cap while walking back and forth between pages, **never selected a decision** (no select action in its log). Runs that do not complete generate no script, so nothing was ever cached or reused. About 82 calls and 310,000 input tokens per record (screenshots). |
| Webwright (Microsoft, commit ff44a05) | not run | 4 (setup check) | | Set up in `../v6-webwright`. Not run: it executes model-written shell commands on the Mac with no sandbox, so it was not run. |
| Plain browser-use 0.13.10 (control) | not run | 1 (setup check) | | Set up in `../v7-browser-use`. Skipped to save quota: v3's live records (about 16-19 calls each) already measure it. |

## What else happened

- **Run #70 overlapped the Skyvern run.** Someone started saved task 34 (4 records) on the main v3 server (:8002, visible
  browser) at 10:36 UTC, while Skyvern was running. It decided INV-26-5751, 2820, 8921 and 9668, all correctly, which at first
  made Skyvern look like 3 / 5. Skyvern's own log shows it saved nothing. Run #70 is also a v3 data point (current code):
  learned a plan, proven on 2 records (this time the live agent filled the date), trusted, extended to a new kind, 4 / 4, 72 calls.
- **After the budget stop, the Skyvern server kept running record 5** (it runs the workflow server-side). It was stopped
  with `start_server.sh stop` and then force-killed. Lesson: stopping a client does not stop a server-side agent.

## v3 weaknesses this showed (not fixed: the comparison did not touch v3's code)

1. **False success on a live record.** INV-26-8921 (run #69): the live agent skipped the payment date, pressed Save and
   Confirm, said "Approve for payment with payment date 14 Nov 2026", and v3 marked the record succeeded. Nothing was saved
   (the site needs the date). The change tracker cannot see a save that the browser never sent.
2. **A right plan thrown away by a wrong live agent.** The proof check treats the live agent as the truth. When the live
   agent's save did not happen (or left a field empty), the comparison should not count as a disagreement; a read-back of
   the record's page would have shown the live agent saved nothing.

## Quota used today (13 Oct)

509 successful calls through the proxy (255 on key …K5EQ, 254 on …q4uA; no daily-limit refusals), plus about 72 by run #70
outside the proxy: about 580 of the roughly 1,000 a day on `gemini-3.5-flash-lite`.

## Caveat

All tools ran on `gemini-3.5-flash-lite`, the model the free quota allows. Stagehand, Skyvern and Webwright are tuned for
stronger models (GPT-5.x, Claude, Gemini Pro); with one of those they would probably finish the task. The comparison answers
"which approach works on the cheap model", which is the constraint v3 was designed for (code does the arithmetic, rule order
and checks; the model only reads and fills small questions).

## Files

- `bench/`: `proxy.js` (call meter, key rotation, pacing), `records.json`, `tasks/`, `grade.py`, `calls.py`, `logs/`
  (`calls.jsonl`, each tool's run output, the v3 bench server log), `v3_workflow.json` (saved task 37).
- `../v4-stagehand`, `../v5-skyvern`, `../v6-webwright`, `../v7-browser-use`: each tool, its own README, `run.sh`, results.
- Nothing in `v1`, `v2`, `v3` or `testsites` was changed. Added to the app's database: saved task 37 and run #69.
