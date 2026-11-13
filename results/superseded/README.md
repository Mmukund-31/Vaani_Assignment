# Superseded runs (kept as raw evidence, excluded from results.csv)

`groq-retrievalA/C-20261113T231457` - first real benchmark of settings A and C (36 requests, 69,587 tokens).
Superseded because two defects were found in them, and Table 3 must compare settings under identical code:

1. Validator false negatives on real gpt-oss output: citations written as full-width brackets (`【P1】`, A3 in
   setting A) or with a zero-width space inside (`[​P1]`, M1 in setting C) were read as "no citation".
   A3's correct answer became "could not verify" after a second model call; M1's second, correct sentence was
   withheld. Fix: `normalise_model_text` in relay/pipeline.py (+ regression test).
2. Benchmark pacing used the single-search prompt size; per-paper re-retrieval made X3's prompt larger, so it
   waited 6.7 s in our own rate limiter and its first token came at 7.7 s. Fix: pace on the planner's prompt.

`standin-daily_exhausted-20261113T233620` - first daily-budget run. Superseded because after the single TPD 429
the governor kept its configured 200K TPD and refilled at 2.3 tok/s, so admission (which checked for 1 token)
let 8 requests through to be rejected inside the pipeline instead of at arrival. Fix: learn the real TPD from
the 429 body ("Limit X, Used Y") and check admission against a realistic request size (1,300 tokens).

`standin-sweep_none_r360-r360-20261114T000045` - interrupted when the session paused (no config snapshot,
not every offered request has a row). Re-run below.

`sweep_r360_overlap/` - all retry-sweep runs at 360/min from 2026-11-14 00:01-00:05. After the session paused,
the assistant stopped the hung load-runner processes but not their parent shell loop, which went on to launch
retry_immediate and retry_backoff at 360/min concurrently with the new chain's runs against the same stand-in
(old immediate 00:01:37-00:02:52 overlaps new none 00:01:48-00:02:51; old backoff 00:02:58-00:04:12 overlaps new
immediate 00:02:58-00:04:13). Shared server load and mid-run /admin/reset calls contaminate both sides. All
r360 runs re-run sequentially; experiments/load_test.py now takes a lock so two runs cannot share the stand-in.
