# The Relay

A small question-answering pipeline over three pinned arXiv papers, with a client-side rate governor,
admission control, retries, and a stand-in model service for load tests beyond Groq's free tier.

```
question -> intake -> search_papers (BM25 tool) -> plan (refuse | per-paper re-retrieve | degrade) ->
            generate (Groq gpt-oss-120b, streamed) -> validate each sentence while streaming -> person
            ^ scheduler: daily budget / queue / predicted-wait admission, FIFO, patience deadline
            ^ governor: RPM/TPM/RPD/TPD token buckets, 429 pause, header-learned limits
```

## Setup (about 5 minutes)

Tested with Python 3.14.4 on Windows 11. macOS/Linux commands are given for convenience but were not tested.

Windows (PowerShell):
```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env       # only needed for real Groq runs
```
macOS / Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env              # only needed for real Groq runs
```
Then (any OS, venv active):
```bash
python -m relay.ingest            # downloads the 3 PDFs at exact versions, verifies, writes papers/manifest.json
python -m pytest -q               # offline: mock provider + stand-in on a local port, no API key needed
```
`.env` holds `GROQ_API_KEY` and `GROQ_MODEL=openai/gpt-oss-120b`; it is git-ignored and never packaged.

## Run

| What | Command |
|---|---|
| Ask offline (mock model) | `python -m relay.cli --mode mock "What is the frame rate of the Mimi codec?"` |
| Ask with Groq | `python -m relay.cli --mode groq "Why scale dot products by sqrt(d_k)?"` |
| Stand-in service | `uvicorn standin.server:app --port 8001` (load runner starts it automatically) |
| Ask via stand-in | `python -m relay.cli --mode standin "..."` |
| Offline retrieval recall | `python -m experiments.retrieval_recall` |
| Groq calibration | `python -m experiments.calibrate models|latency|rpm-probe|summarize` (spends quota) |
| Retrieval benchmark | `python -m experiments.retrieval_bench --mode groq --settings A B C D` (spends quota) |
| Load scenarios (stand-in) | `python -m experiments.load_test --scenario all` / `--scenario retry_all` |
| Small real load run | `python -m experiments.load_test --scenario steady --mode groq --rate 3 --minutes 4` |
| Merge raw results | `python -m scripts.build_results` -> `results/results.csv` |
| Tables from CSV | `python -m scripts.make_tables` -> `results/summaries/tables.md` |
| Grading sheet | `python -m scripts.grading_sheet` -> `evaluation/grading_sheet.md` |
| Independent checks | `python -m scripts.benchmark_accounting` / `python -m scripts.crosscheck_tables` |
| Rebuild everything | `python -m scripts.finalize` (CSV -> tables -> report -> PDF -> package -> validation) |
| Submission checks | `python -m scripts.validate_submission` |

## Layout

- `relay/` pipeline (`pipeline.py`), retrieval (`retrieval.py`), governor (`limits.py`), scheduler (`scheduler.py`),
  providers (`providers.py`), CSV/trace writer (`records.py`), ingestion (`ingest.py`)
- `standin/server.py` stand-in model service (FastAPI, SSE)
- `experiments/` calibration, retrieval benchmark, load runner
- `evaluation/` 18 questions with references, grading guidelines, grades
- `results/` `results.csv` (merged), `runs/` per-run CSV + config snapshot, `traces/` per-request stage traces,
  `raw/` calibration log, `superseded/` runs replaced after bugs were found (kept as evidence)

## Conventions

- Page numbers are 1-based PDF page indices. Chunk sizes are cl100k tokens (approximate for Llama/gpt-oss).
- `first_token_ms` = arrival -> first validated sentence released to the person; `model_ttft_ms` is the provider's.
- Empty CSV field = not applicable / not measured for that request (e.g. tokens for requests rejected before a
  model call; answer_grade for stand-in rows).
- Source `real` = Groq; `stand-in` = our service. Never mixed within a row.

- Edit `docs/report_source.md`, not `report.md`: the report is generated from it with tables pulled from `results.csv`.

See `DECISIONS.md` for design choices and `report.md` for results.
