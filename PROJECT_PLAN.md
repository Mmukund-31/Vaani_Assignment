# The Relay — Implementation Plan

## Context
Vaani ML-intern assignment "The Relay" (brief attached in chat, 4 pages; it is the source of truth). Build a Python pipeline that answers questions only from three pinned arXiv papers (1706.03762v7, 2410.00037v1, 2507.07955v2) with citations, using Groq free tier for real calls plus a self-written stand-in model service for load. Deliver: code, `results.csv` (exact 12 columns), 4 required tables, ≤2-page report with the exact headings, conversation history, submission folder.

Workspace `c:\Users\mekal\OneDrive\Desktop\Vaani_Assignment` is **empty**, not a git repo. Python available only via `py` launcher (3.14.4). No existing code to reuse.

## Key constraints that shape the design
- **Groq daily quota is the scarcest resource.** Rough budget (to be verified from console + headers, not assumed): retrieval benchmark 3 settings × 18 Qs × ~2 calls × ~2.5K tokens ≈ 250K tokens. That exceeds the 70B models' typical free TPD, so the model choice is made *after* calibration headers are read. Plan: spread real work over ≥2 days if needed; cache every real response (keyed by prompt hash) so reruns never re-spend quota.
- Stand-in handles all 5 load scenarios at volume; real Groq gets a small steady run (~15–20 reqs) so Table 2a has at least one "real" row.
- Never fabricate: anything not measured → `not measured` in tables.

## Architecture (simplified from the suggested tree — no empty modules)
```
relay/
  config.py            env + settings (GROQ_API_KEY, GROQ_MODEL, PROVIDER=groq|standin|mock)
  ingest.py            download pinned PDFs, sha256, manifest.json, PyMuPDF page text + section headings
  chunker.py / retriever.py   section-aware chunks w/ page+section metadata, BM25 (rank_bm25), optional rerank
  providers.py         Provider interface: stream(messages) -> async iter of chunks + usage + headers
                       GroqProvider (httpx SSE to OpenAI-compatible endpoint), StandInProvider (same wire format), MockProvider
  pipeline.py          stages: intake → retrieve (tool) → plan/answerability (branch + ≤1 re-retrieve loop) → generate (stream) → validate (deterministic citation check, ≤1 regen or safe refusal)
  scheduler.py         bounded asyncio queue, admission control, RPM/TPM/RPD/TPD sliding windows w/ reservation+reconcile, concurrency cap, deadlines
  retry.py             classify retryable, exp backoff + full jitter, honor Retry-After, deadline-aware; "immediate" mode for comparison
  tracing.py           per-request stage timestamps (monotonic) → stage_traces.jsonl; CSV row writer
standin/server.py      FastAPI SSE service: lognormal TTFT + per-token rate from calibration, load-dependent multiplier on active reqs, tail spikes, seeded faults, 429 w/ retry-after + x-ratelimit headers mimicking Groq, RPM/TPM/RPD/TPD/concurrency limits, reset
experiments/           calibrate.py, retrieval_bench.py, load_test.py (scenario configs as JSON), retry_amplification.py, make_tables.py (all tables from CSV)
evaluation/questions.json (18 Qs: 4/4/4 per paper, 3 cross-paper, 3 unanswerable) + grading_guidelines.md + grades.json
scripts/validate_submission.py
tests/                 pytest unit + integration with mock provider, fixed seeds; real-Groq tests skipped w/o key
docs: README.md, DECISIONS.md, PROGRESS.md, REQUIREMENTS_TRACEABILITY.md, report.md
```
FastAPI app for the pipeline itself is omitted (CLI + load runner call the pipeline directly in-process) — the brief doesn't require an HTTP front door; recorded in DECISIONS as a deliberate omission.

**Model calls per request: 2 by default** (plan/answerability classifier is cheap & deterministic-first using BM25 score thresholds + a small LLM call only when ambiguous; generator; validation deterministic). Optional LLM verifier measured once on the eval set to justify keeping/dropping it.

## Phases & acceptance criteria
0. **Audit/init** — traceability matrix from brief, PROJECT_PLAN/PROGRESS/DECISIONS, venv, requirements, `.gitignore` (.env), `git init`. ✔ when docs exist and `pytest` runs (0 tests).
1. **Ingestion** — download 3 PDFs at exact versions, verify title/version from PDF text + arXiv abs page, manifest w/ sha256/size/url/time; per-page text + heading detection. ✔ manifest valid, tests on page metadata.
2. **Retrieval** — chunker (unit = tokens via tiktoken cl100k approx, documented), BM25, rerank (section/title-aware boost + query-term coverage), 3 settings (~300/k3/no, ~600/k4/no, ~900/k5/yes, adjustable). Write the 18 questions + ground truth by reading the papers. Offline recall@k on gold pages (no API cost). ✔ tests pass, recall table.
3. **Groq calibration (needs your key in `.env`)** — list models, pick model, ~30–40 controlled streaming requests: latency spread (TTFT, total, tok/s), record all `x-ratelimit-*` headers + `retry-after`; one small controlled burst to observe a 429 and recovery (RPM only — never exhaust daily). Save `results/groq_calibration.json`. ✔ Table 1 fillable with evidence + confidence.
4. **Pipeline end-to-end** — mock first, then a handful of real questions. ✔ integration tests (answerable, unanswerable, re-retrieve, bad-citation rejection, timeout, 429).
5. **Stand-in** — calibrated from step 3 JSON; all 15 required capabilities; unit tests per capability with seeds. Document where it differs most (e.g., no real token-length-dependent prefill, no Groq's hidden queueing, deterministic text).
6. **Scheduler/admission/retries** — bounded queue, limiters, daily budget behaviour (stop dispatch, return capacity message w/ reset time), retry policy. ✔ performance tests: concurrency bound, no leaked tasks, reservations reconcile.
7. **Experiments** — retrieval benchmark on real Groq (3 settings × 18, cached), manual grading (I draft grades against the written rubric; **you review and sign off**), real steady run, stand-in scenarios: steady, burst, sustained overload, RPM halved, daily exhausted, + retry amplification (immediate vs backoff). All rows → `results/results.csv` with supplementary columns after the 12 required.
8. **Tables + report** — `make_tables.py` regenerates Tables 1/2a/2b/3 from CSV/JSON; slow-request breakdown from traces; report.md in exact heading order (1–5, Retrieval, Mislead, 6–8, Vaani feedback, How I worked with the assistant); export PDF; ≤2 pages of prose checked.
9. **Submission audit** — `validate_submission.py` (CSV schema/enums/uniqueness, tables recomputed match, no secrets grep), assemble `The_Relay_Submission/`. Drive upload is done by you (I can't share on your behalf without confirmation).

## Decisions confirmed with user
- Groq key exists; user pastes it into `.env` themselves at Phase 3 (never in chat/source).
- Pipeline runs in-process + CLI; no FastAPI front door (stand-in remains a separate FastAPI service).

## Things only you can provide (I will not fabricate)
- Groq API key in `.env` (needed at Phase 3).
- Vaani website feedback opinions and "what is distinctly mine / what I overruled" — I'll fetch the site and draft observations, but the opinions and overrule story must be yours.
- Conversation history export from this tool.
- Final sign-off on answer grades.

## Verification
- `py -3.14 -m pytest -q` (unit + integration, mock/stand-in only), `ruff check .`
- `python -m relay.cli ask --provider mock "..."` and `--provider groq` smoke test
- `python -m experiments.load_test --scenario burst` against `uvicorn standin.server:app --port 8001`
- `python -m experiments.make_tables` output diffed against tables in report.md
- `python -m scripts.validate_submission` passes
