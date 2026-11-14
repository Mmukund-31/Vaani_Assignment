# Progress

| Phase | Status | Evidence |
|---|---|---|
| 0 Audit / init | done | venv (Python 3.14.4), requirements.txt pinned, .gitignore excludes .env, git init |
| 1 Ingestion | done | `python -m relay.ingest`; papers/manifest.json: 3 PDFs, sha256, abs-page title + version check, title on PDF p1 all true |
| 2 Retrieval | done (offline) | 4 settings A-D; `python -m experiments.retrieval_recall` -> gold-page recall 0.87 for all settings, misses X2/X3 (cross-paper) for all |
| 4 Pipeline (mock) | done | 27/27 pytest pass (`python -m pytest -q`), ruff clean |
| 3 Groq calibration | done (2026-11-13) | 55 real requests, 28,958 tokens; results/groq_calibration.json, results/raw/groq_calibration_raw.jsonl |
| 4 Pipeline (real) | done | relay/cli.py; 2 real smoke questions |
| 5 Stand-in | done | standin/server.py, 9 capability tests |
| 6 Scheduler/governor | done | token buckets, token-aware admission, degraded retrieval, TPD learned from 429 body |
| 7a Retrieval benchmark (real) | A, B, C, D: 18 questions each | results/runs/groq-retrieval* |
| 7b Stand-in scenarios | done (v2, consistent queue metric) | results/runs/standin-{steady,burst,overload,rpm_halved,daily_exhausted}-20261114* |
| 7c Retry experiment | done: 90/min x3 policies + sweep 150/240/360 x3 | results/runs/standin-retry_*, standin-sweep_* |
| 7d Real steady load row | done (12 offered; daily budget ran out mid-run) | results/runs/real-steady-* |
| 7e Grading | DRAFT for A, B, C, D (72 answers) - needs student review | evaluation/grades.json |
| 8 Report | done except student-only sections | report.md / report.pdf (built by scripts/build_report.py) |
| 9 Submission audit | validate_submission OK | The_Relay_Submission/ |

## Issues found and fixed
- Heading detection missed Attention/H-Net sections (number and title are separate bold lines in those PDFs) -> pair bold number line with next bold line.
- Span joining dropped whitespace spans, gluing words ("3.2TheHelium...") -> join all spans, test boldness on non-blank spans only.
- Moshi appendix (pp. 53-66) and Attention appendix were labelled "References" and would have been dropped by the reference filter -> detect "Appendix X." and "Attention Visualizations".
- Run-in paragraph labels ("Discussion.", "Related Work.") were overriding the real section -> unnumbered names ending in "." are not headings.
- Mock provider read only the last message, so regeneration feedback broke it -> read all user messages.
- Governor used a 60 s sliding window; Groq measured as continuous token buckets -> rewrote as buckets.
- Burst smoke: 22/30 waited 8-11 s then were rejected -> token-aware admission at arrival + degraded retrieval.
- Validator rejected real gpt-oss citations written as full-width brackets or with zero-width spaces -> normalise.
- Benchmark pacing ignored per-paper re-retrieval prompt size (X3 waited 6.7 s in our limiter) -> pace on planner prompt.
- Org id appeared in Groq 429 bodies in raw logs -> redacted.
- Stand-in synthetic answers split sentences differently from the pipeline -> reuse pipeline's SentenceGate.
- Stand-in release task captured global state (broken after /admin/reset) -> bind to the admitting state.
- After a TPD 429 the governor kept the configured TPD and leaked requests past admission -> learn TPD from 429 body.
- Peak queue counted only slot waiters (always 1) while real waiting was in the governor -> backlog = both.
- Interrupted sweep: killing the hung runner left its parent loop alive; it launched overlapping 360/min runs against the
  same stand-in, producing a false "retry storm". Contaminated runs superseded, re-run sequentially, lock added.
- Real daily token limit (200,000) reached during benchmark D; benchmark now stops cleanly on daily exhaustion.
- Provider error text was not stored in traces -> error_detail field added.
- Setting D completed (U2, U3) by resuming run groq-retrievalD-20261114T073933 with `--resume --questions`, same account
  and key. Fixed: resume would have reused the request id of the logged daily 429 (duplicate id) -> attempt numbering
  continues from existing rows. Observed: our admission estimate was below Groq's charge, so the last daily wait woke
  early 10 times (10 extra, free, daily 429s logged as retrieval_D_budget_wait).

## Final audit (2026-11-14) - see FINAL_AUDIT.md
- report.md had student edits made directly in the generated file (still showing D partial); sections ported
  verbatim into docs/report_source.md and the report rebuilt from it.
- Independent checks added: scripts/benchmark_accounting.py (one final response per question) and
  scripts/crosscheck_tables.py (26 cell groups recomputed, 0 mismatches).
- D/H1 gold-page discrepancy resolved from the paper text (p.17 added to H1 gold pages, disclosed).
- PDF layout: 3 pages, no split tables, stray header fills removed. pandas removed (unused).
