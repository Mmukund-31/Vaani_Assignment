# Requirements traceability (from The_Relay_Assignment_Brief.pdf)

| # | Requirement (brief) | Where satisfied | Status |
|---|---|---|---|
| R1 | Python/C++/Go | Python 3.14 | done |
| R2 | >= 4 steps | relay/pipeline.py: intake, retrieve, plan, generate, validate | done |
| R3 | >= 1 tool call | `Pipeline.search_papers` (BM25 tool) | done |
| R4 | >= 1 branch or loop | plan: refuse-no-evidence branch; per-paper re-retrieve loop; regenerate-once loop | done |
| R5 | One step retrieves passages from the library | relay/retrieval.py | done |
| R6 | Exact paper versions | relay/ingest.py, papers/manifest.json | done |
| R7 | Every answer cites where | label->citation rendering + validator | done (mock); real eval pending |
| R8 | Say so when papers don't say | NOT_IN_LIBRARY path + no-evidence branch | done (mock); real eval pending |
| R9 | Real Groq free-tier model | experiments/calibrate.py, OpenAICompatProvider | pending key |
| R10 | Find limits from docs + 429 behaviour | calibrate rpm-probe / tpm-probe | pending key |
| R11 | Stand-in: slow, fail, 429, slower under load, streams | standin/ | pending |
| R12 | Stand-in calibrated from real measurements; say where it differs | standin config from groq_calibration.json | pending |
| R13 | >= 3 load shapes; 5 table rows (steady, burst, overload, RPM halved, daily exhausted) | experiments/load_test.py | pending |
| R14 | Keep raw results for real + stand-in | results/results.csv, results/raw/ | pending |
| R15 | Table 1 limits | report | pending |
| R16 | Table 2a/2b latency + outcomes | report, generated from results.csv | pending |
| R17 | Table 3 >= 3 retrieval settings | settings A-D | pending real eval |
| R18 | >= 15 own questions, quality + tokens per answer | evaluation/questions.json (18) | questions done |
| R19 | Slow request breakdown waiting/working/retrying, before/after first token | stage_traces.jsonl | pending |
| R20 | Per-minute vs per-day policy | relay/limits.py, relay/scheduler.py | code done |
| R21 | Retry policy + case where retry made it worse | experiments/retry_amplification | pending |
| R22 | 3pm daily-budget-gone reply | scheduler admission message | code done |
| R23 | Three ways library misleads naive retriever | report | pending |
| R24 | Vaani feedback | report (user's own opinions) | pending - user |
| R25 | How I worked with assistant + real history | report + export | pending - user |
| R26 | results.csv exact 12 columns | experiments/ writer + scripts/validate_submission.py | pending |
| R27 | Max 2 pages writing | report | pending |
| R28 | No key in submission | .gitignore, validate_submission secret scan | partial |
