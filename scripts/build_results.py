"""Merge every run into results/results.csv (one row per offered request) and attach answer grades.

  python -m scripts.build_results

Sources:
- results/runs/*.csv                         pipeline requests (real benchmark/load runs and stand-in runs)
- results/raw/groq_calibration_raw.jsonl     raw calibration probes against real Groq (scenario=calibration)
- evaluation/grades.json                     {request_id: grade} written after manual review
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from relay.records import COLUMNS

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "results.csv"
GRADES = ROOT / "evaluation" / "grades.json"


def calibration_rows() -> list[dict]:
    path = ROOT / "results" / "raw" / "groq_calibration_raw.jsonl"
    rows = []
    if not path.exists():
        return rows
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        r = json.loads(line)
        if "status" not in r:
            continue  # derived result lines, not requests
        status = r["status"] if r["status"] in ("ok", "rate_limited", "timeout") else "error"
        ok = status == "ok"
        rows.append({c: "" for c in COLUMNS} | {
            "request_id": f"calib-{n:04d}", "scenario": f"calibration_{r['phase']}", "source": "real",
            "start_time": r["start_utc"], "first_token_ms": round(r["ttft_ms"], 1) if ok and r.get("ttft_ms") else "",
            "total_ms": round(r["total_ms"], 1), "status": status, "retries": 0,
            "tokens_in": r.get("tokens_in") or "" if ok else "", "tokens_out": r.get("tokens_out") or "" if ok else "",
            "retrieval_setting": f"probe:{r['kind']}", "answer_grade": "", "run_id": "calibration",
            "decision": "probe", "error_type": r.get("error_type", ""), "model_calls": 1,
            "reasoning_tokens": r.get("reasoning_tokens") or "" if ok else "", "http_status": r.get("http_status") or "",
            "model": r.get("model", ""), "model_ttft_ms": round(r["ttft_ms"], 1) if ok and r.get("ttft_ms") else ""})
    return rows


def main():
    grades = json.loads(GRADES.read_text(encoding="utf-8")) if GRADES.exists() else {}
    rows = calibration_rows()
    for f in sorted((ROOT / "results" / "runs").glob("*.csv")):
        with f.open(encoding="utf-8") as fh:
            rows.extend(csv.DictReader(fh))
    for r in rows:
        g = grades.get(r["request_id"])
        if g:
            r["answer_grade"] = g["grade"] if isinstance(g, dict) else g
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUT.relative_to(ROOT)} ({sum(1 for r in rows if r['answer_grade'])} graded)")


if __name__ == "__main__":
    main()
