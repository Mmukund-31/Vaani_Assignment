# Requirements traceability (from The_Relay_Assignment_Brief.pdf)

| # | Requirement (brief) | Where satisfied | Status |
|---|---|---|---|
| R1 | Python/C++/Go | Python 3.14 | done |
| R2 | >= 4 steps | relay/pipeline.py: intake, retrieve, plan, generate, validate | done |
| R3 | >= 1 tool call | `Pipeline.search_papers` (BM25 tool) | done |
| R4 | >= 1 branch or loop | plan: refuse-no-evidence branch; per-paper re-retrieve loop; regenerate-once loop | done |
| R5 | One step retrieves passages from the library | relay/retrieval.py | done |
| R6 | Exact paper versions | relay/ingest.py, papers/manifest.json | done |
| R7 | Every answer cites where | label->citation rendering + validator | done - 72.2% correct and cited (setting C); label->citation rendering makes citations to non-existent pages impossible |
| R8 | Say so when papers don't say | NOT_IN_LIBRARY path + no-evidence branch | done - all 3 unanswerable refused in A, B, C |
| R9 | Real Groq free-tier model | experiments/calibrate.py, OpenAICompatProvider | done - openai/gpt-oss-120b, 128 real rows in results.csv |
| R10 | Find limits from docs + 429 behaviour | calibrate rpm-probe / tpm-probe | done - Table 1; RPM, TPM and TPD 429s observed with bodies |
| R11 | Stand-in: slow, fail, 429, slower under load, streams | standin/ | done - standin/server.py + 9 tests |
| R12 | Stand-in calibrated from real measurements; say where it differs | standin config from groq_calibration.json | done - report section 2 |
| R13 | >= 3 load shapes; 5 table rows (steady, burst, overload, RPM halved, daily exhausted) | experiments/load_test.py | done - steady, burst, overload, RPM halved, daily exhausted (+ retry runs) |
| R14 | Keep raw results for real + stand-in | results/results.csv, results/raw/ | done - results/runs, results/traces, results/raw |
| R15 | Table 1 limits | report | done |
| R16 | Table 2a/2b latency + outcomes | report, generated from results.csv | done (generated) |
| R17 | Table 3 >= 3 retrieval settings | settings A-D | done - A, B, C (18 questions each), D (16 questions) |
| R18 | >= 15 own questions, quality + tokens per answer | evaluation/questions.json (18) | done - 18 questions; grades are DRAFT pending student review |
| R19 | Slow request breakdown waiting/working/retrying, before/after first token | stage_traces.jsonl | done - report section 4 |
| R20 | Per-minute vs per-day policy | relay/limits.py, relay/scheduler.py | done - report section 5 |
| R21 | Retry policy + case where retry made it worse | experiments/retry_amplification | done - report section 6 |
| R22 | 3pm daily-budget-gone reply | scheduler admission message | done - report section 7 |
| R23 | Three ways library misleads naive retriever | report | done - results/summaries/naive_retriever.md |
| R24 | Vaani feedback | report (user's own opinions) | PENDING - student |
| R25 | How I worked with assistant + real history | report + export | PENDING - student (export + half page) |
| R26 | results.csv exact 12 columns | experiments/ writer + scripts/validate_submission.py | done - validate_submission OK |
| R27 | Max 2 pages writing | report | prose ~2 pages; tables add ~1 page |
| R28 | No key in submission | .gitignore, validate_submission secret scan | done - validator secret scan |
