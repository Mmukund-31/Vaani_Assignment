# Final audit (2026-11-14)

Scope: every requirement in `The_Relay_Assignment_Brief.pdf`, checked against current files, not progress notes.
No Groq calls were made during the audit. Status: PASS / FAIL / NEEDS REVIEW / N/A.

## Requirements

| # | Requirement (brief) | Evidence | Verification | Status | Action |
|---|---|---|---|---|---|
| 1 | Python, C++ or Go | `relay/`, `standin/` (Python 3.14.4) | inspection | PASS | - |
| 2 | >= 4 steps | `relay/pipeline.py`: intake, search_papers, plan, generate, validate | code + tests | PASS | - |
| 3 | >= 1 tool call | `Pipeline.search_papers` (BM25 tool) | code + tests | PASS | - |
| 4 | >= 1 branch or loop | no-evidence refusal branch; per-paper re-retrieval loop; one regeneration | `test_comparison_question_retrieves_per_paper`, `test_no_lexical_evidence_refuses_without_model_call`, `test_uncited_first_sentence_triggers_one_regeneration` | PASS | - |
| 5 | A step retrieves passages from the library | `relay/retrieval.py` | tests | PASS | - |
| 6 | Exact paper versions | `papers/manifest.json` (1706.03762v7, 2410.00037v1, 2507.07955v2) | `python -m relay.ingest` re-verified arXiv abs title + version + PDF title; SHA-256 recomputed | PASS | - |
| 7 | Every answer cites where it came from | label -> citation rendering; per-sentence validator | 72 graded answers; every citation carries the pinned version | PASS (caveat) | Validator proves label validity, not semantic support; 5 answers passed it but were graded wrong (stated in report section 1) |
| 8 | Say so when the papers do not say | NOT_IN_LIBRARY path, no-evidence branch | 12/12 unanswerable questions refused across A-D (`benchmark_accounting.md`) | PASS | - |
| 9 | Real hosted Groq free-tier model | `openai/gpt-oss-120b`; 156 real rows (57 calibration, 87 benchmark incl. 15 daily-429 attempts, 12 real load) | `results.csv` source=real | PASS | - |
| 10 | Find limits from docs and from what it returns | Table 1; `results/raw/groq_calibration_raw.jsonl` | for all 6 calibration 429s, (used + requested - limit) / refill rate equals the provider's stated wait exactly | PASS | - |
| 11 | Stand-in: slow, fail, refuse at limit, slower under load, streams | `standin/server.py` | `tests/test_standin.py` (9 tests) + HTTP smoke test | PASS | - |
| 12 | Stand-in calibrated from real measurements; where it differs most | report section 2 | TTFT fit from calibration; load slowdown and abandoned-work model stated as assumptions | PASS | - |
| 13 | Modest real requests first | 57 calibration requests (51 ok, 6 x 429) | raw log | PASS | - |
| 14 | >= 3 load shapes; rows steady, burst, overload, per-minute halved, daily exhausted | `results/runs/standin-*`, `real-steady-*`; Tables 2a/2b | `scripts.crosscheck_tables` | PASS | - |
| 15 | Keep raw results for real and stand-in | `results/runs`, `results/traces`, `results/raw`, `results/superseded` | inspection | PASS | - |
| 16 | Required headings in order + 4 added headings | `report.md` | inspection of rendered PDF | PASS | - |
| 17 | Table 1 / 2a / 2b / 3 completely filled | `results/summaries/tables.md`, `report.md` | 26 recomputed cell groups match (`scripts.crosscheck_tables`) | PASS | - |
| 18 | Percentiles named, sample sizes stated | Table 2a "Latency samples (n)"; report section 3 | small-n rows called out (real steady n=3, burst n=4, daily n=6) | PASS | - |
| 19 | A number we could not have guessed | report section 3 (rolling daily bucket; 298-token admission charge) | raw log | PASS | - |
| 20 | Quality on >= 15 own questions + tokens per answer | 18 questions x 4 settings, 72 grades | `scripts.benchmark_accounting`: one final response per question, no ungraded or duplicate finals | NEEDS REVIEW | Grades are DRAFT; student sign-off pending (9 flagged judgment calls) |
| 21 | Slow request: waiting / working / retrying, before/after first token | report section 4; trace `groq-retrievalA-...-X3` | events_ms in `stage_traces.jsonl` | PASS | - |
| 22 | Per-minute and per-day policy: delayed / dropped / weaker answer | report section 5 | load runs; Table 2b splits provider 429 vs local rejection | PASS | - |
| 23 | Retrieval: >= 3 settings, choice, behaviour near the limit | Table 3 (A-D, 18 questions each), degraded retrieval | accounting + crosscheck | PASS | - |
| 24 | Three ways the library misleads a naive retriever | `results/summaries/naive_retriever.md`, report | regenerated offline | PASS | - |
| 25 | Retry policy + one case where retrying made things worse | report section 6, retry sweep, real X3 | runs + trace | PASS | - |
| 26 | What the person sees at 3 pm | report section 7 (verbatim message from real run) | trace | PASS | - |
| 27 | Unsure / left out / learned | report section 8 (learning text written by the student) | inspection | PASS | - |
| 28 | Feedback on Vaani | report (written by the student) | inspection | PASS | - |
| 29 | How I worked with the assistant + real history | `AI_CONVERSATION_HISTORY.md` + report section (student-owned; not modified by the audit) | inspection only | NEEDS REVIEW | Student to confirm the half page is consistent with the exported history (see "Manual actions") |
| 30 | `results.csv`, 12 columns, one row per request, allowed values | `results/results.csv` (3,095 rows) | `scripts.validate_submission` OK; config `offered` = rows for every load run | PASS | - |
| 31 | Every table figure computable from `results.csv` | Tables 2a/2b/3 | `scripts.crosscheck_tables` | PASS (note) | Table 1 limit values come from headers/bodies in `groq_calibration_raw.jsonl` (the CSV has the calibration rows and statuses, not header values) |
| 32 | Max 2 pages of writing | `report.pdf` = 3 pages (prose plus 5 required tables; body 8.4 pt) | rendered + inspected | NEEDS REVIEW | Prose alone is about 2 pages; tables add about 1. Student decides whether to trim further |
| 33 | Do not include the key | `.env` git-ignored, excluded from package | validator secret scan (key pattern, organization id) | PASS | - |
| 34 | Own account, no tricks around limits | one account and key throughout (confirmed by the student); D completed by waiting for the rolling daily bucket | raw logs | PASS | - |

## Benchmark accounting (from raw evidence)

`results/summaries/benchmark_accounting.md`. Each setting: 18 questions (15 answerable, 3 unanswerable), one final response
each. D has 33 attempt rows = 18 final responses + 15 daily-budget attempts, all provider 429s (scenario
`retrieval_D_budget_wait`, excluded from quality figures): X3 needed 2 attempts, U2 3, U3 13. Final responses
A-D: correct 9 / 11 / 13 / 13, wrong 2 / 3 / 2 / 2, refused_correctly 3 / 3 / 3 / 3, refused_wrongly 4 / 1 / 0 / 0.

## Defects found and fixed in this audit

| Defect | Impact | Fix | Regression test |
|---|---|---|---|
| Stale `report.md`: student edits were made in the generated file, which still showed D as partial (16/18) | Report contradicted tables | Student sections ported verbatim into `docs/report_source.md` (byte-identical check), report rebuilt | - |
| Resumed benchmark would reuse the request id of a logged daily-429 attempt | Duplicate `request_id` | Attempt numbering continues from existing rows | `test_resumed_benchmark_keeps_request_ids_unique` |
| First-token timeout used the deadline measured before the governor wait | Latent; total timeout already capped it, no recorded result affected | Recompute remaining time after the wait | `test_first_token_timeout_uses_time_left_after_limiter_wait` |
| Requests shed at arrival received an empty reply | Person told nothing | Busy / failure notice without setting first_token_ms; handles infinite waits | `test_shed_requests_are_told_why_without_a_first_token`, `test_busy_notice_handles_unbounded_wait` |
| Table 2b merged provider 429s with local rejections | Misleading | Split column "provider 429 / rejected locally" | crosscheck |
| D/H1 gold-page discrepancy | Grade rule vs evidence | p.17 text supports the claim; added to H1 gold pages with a disclosed audit note | accounting |
| PDF: stray grey table-header fills on later pages; tables split across pages; 4 pages | Presentation | Header fill removed, explicit page breaks before two tables, 90/min retry rows moved to `tables.md`, prose trimmed: 3 pages | visual inspection |
| `pandas` in requirements but unused (and blocked by Application Control on this machine) | Unneeded dependency | Removed | full suite |
| README claimed "any Python >= 3.12 should work" (untested); no macOS/Linux steps | Unverified claim | States tested version; per-OS commands, macOS/Linux marked untested | - |
| `DECISIONS.md` setting-A cost from a superseded run (1.46K) | Stale number | 1,495 (Table 3) | - |

## Verification commands (run 2026-11-14, this machine)

| Command | Result |
|---|---|
| `python -m pytest -q` | 46 passed, 0 failed, 0 skipped |
| `python -m ruff check relay standin experiments scripts tests --select E,F,W --ignore E501` | All checks passed |
| type checking | not configured (mypy not installed) |
| `python -m relay.ingest` + SHA-256 recompute | 3/3 versions verified, hashes match |
| `python -m scripts.build_results` | 3,095 rows, 72 graded |
| `python -m scripts.benchmark_accounting` | problems: none |
| `python -m scripts.crosscheck_tables` | 26 cell groups, 0 mismatches |
| `python -m scripts.validate_submission` (working tree and package) | OK |
| `python -m relay.cli --mode mock ...` | answer, citation rendered |
| stand-in HTTP smoke (`relay.cli --mode standin`) | answer, 1 request served |
| `python -m experiments.retrieval_recall` | gold-page recall 0.87 for A-D (misses X2, X3) |
| report PDF | 3 pages, inspected visually |

Environment note: `pandas` could not be imported on this machine (Windows Application Control blocked a DLL); it is
not used by the project and has been removed from requirements.

## Manual actions (student only)

1. Review the 72 draft grades, especially the 9 flagged; then set `_meta.status` in `evaluation/grades.json` to FINAL.
2. Re-read "How I worked with the assistant" against `AI_CONVERSATION_HISTORY.md`: reviewers will compare them.
   In the history, the overlapping retry runs were first identified by the assistant; the brief asks for "one
   decision where you overruled it" (the section currently describes a decision retained); and the grades
   described as checked are still drafts.
3. Decide whether a 3-page PDF is acceptable or trim further.
4. Commit, upload `The_Relay_Submission/` to Google Drive, set sharing.
