"""Write one CSV row + one trace line per offered request.

Each run writes results/runs/<run_id>.csv and results/traces/<run_id>.jsonl; scripts/build_results.py merges
all runs into results/results.csv. Nothing is overwritten.

Missing-value convention: a field is left EMPTY when the measurement does not exist for that request
(e.g. first_token_ms for a request rejected at admission, tokens for a request that never reached a model,
answer_grade for stand-in rows whose text is synthetic and therefore not graded).
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .pipeline import RequestRecord
from .retrieval import SETTINGS

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ["request_id", "scenario", "source", "start_time", "first_token_ms", "total_ms", "status", "retries",
            "tokens_in", "tokens_out", "retrieval_setting", "answer_grade"]
EXTRA = ["run_id", "question_id", "decision", "error_type", "queue_wait_ms", "limiter_wait_ms", "retry_wait_ms",
         "working_ms", "retrieval_ms", "model_ms", "model_ttft_ms", "model_calls", "retrieval_rounds", "queue_depth", "degraded",
         "reasoning_tokens", "http_status", "model", "deadline_ms", "offered_at_s", "seed"]
COLUMNS = REQUIRED + EXTRA


def _f(x, nd=1):
    return "" if x is None else round(float(x), nd)


def row(rec: RequestRecord, *, run_id: str, scenario: str, source: str, setting_label: str, model: str,
        question_id: str = "", grade: str = "", deadline_s: float | None = None, offered_at_s: float | None = None,
        seed: int | str = "") -> dict:
    reached_model = rec.model_calls > 0
    return {
        "request_id": f"{run_id}-{rec.request_id}", "scenario": scenario, "source": source,
        "start_time": rec.start_time, "first_token_ms": _f(rec.first_token_ms), "total_ms": _f(rec.total_ms),
        "status": rec.status, "retries": rec.retries,
        "tokens_in": rec.tokens_in if reached_model else "", "tokens_out": rec.tokens_out if reached_model else "",
        "retrieval_setting": SETTINGS[rec.setting_used].label() if rec.setting_used else setting_label,
        "answer_grade": grade,
        "run_id": run_id, "question_id": question_id, "decision": rec.decision, "error_type": rec.error_type,
        "queue_wait_ms": _f(rec.queue_wait_ms), "limiter_wait_ms": _f(rec.limiter_wait_ms),
        "retry_wait_ms": _f(rec.retry_wait_ms), "working_ms": _f(rec.working_ms), "retrieval_ms": _f(rec.retrieval_ms, 2),
        "model_ms": _f(rec.model_ms), "model_ttft_ms": ";".join(str(x) for x in rec.model_ttft_ms),
        "model_calls": rec.model_calls, "retrieval_rounds": rec.retrieval_rounds, "queue_depth": rec.queue_depth, "degraded": int(rec.degraded),
        "reasoning_tokens": rec.reasoning_tokens if reached_model else "", "http_status": rec.http_status or "",
        "model": model if reached_model else "", "deadline_ms": _f(deadline_s * 1000 if deadline_s else None, 0),
        "offered_at_s": _f(offered_at_s, 3), "seed": seed,
    }


def trace(rec: RequestRecord, run_id: str, extra: dict | None = None) -> dict:
    d = {"request_id": f"{run_id}-{rec.request_id}", "question": rec.question, "status": rec.status,
         "decision": rec.decision, "error_type": rec.error_type, "events_ms": rec.events,
         "answer": rec.answer, "answer_raw": rec.answer_raw, "citations": rec.citations, "retrieved": rec.retrieved,
         "sentences_released": rec.sentences_released, "sentences_rejected": rec.sentences_rejected,
         "breakdown_ms": {"queue_wait": round(rec.queue_wait_ms, 1), "limiter_wait": round(rec.limiter_wait_ms, 1),
                          "retry_wait": round(rec.retry_wait_ms, 1), "retrieval": round(rec.retrieval_ms, 2),
                          "model": round(rec.model_ms, 1), "total": round(rec.total_ms or 0, 1)}}
    if extra:
        d.update(extra)
    return d


class RunWriter:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.csv_path = ROOT / "results" / "runs" / f"{run_id}.csv"
        self.trace_path = ROOT / "results" / "traces" / f"{run_id}.jsonl"
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        if self.csv_path.exists():
            raise FileExistsError(f"{self.csv_path} exists; run ids must be unique")
        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, COLUMNS).writeheader()

    def write(self, r: dict, t: dict) -> None:
        with self.csv_path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, COLUMNS).writerow(r)
        line = re.sub(r"org_[0-9a-z]{10,}", "org_REDACTED", json.dumps(t, ensure_ascii=False))
        with self.trace_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
