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
