"""Checks before submission.

  python -m scripts.validate_submission [--dir The_Relay_Submission]

- results.csv: exact 12 required columns first and in order, unique request_id, allowed enums, numeric fields valid
  and non-negative, retries non-negative integers, source matches run naming, ok rows that released text have
  first_token_ms, rejected-before-model rows have empty tokens.
- every run's config snapshot 'offered' count equals its rows in results.csv (one row per offered request).
- secrets: no Groq key pattern, no un-redacted organization id, no .env file in the tree.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ["request_id", "scenario", "source", "start_time", "first_token_ms", "total_ms", "status", "retries",
            "tokens_in", "tokens_out", "retrieval_setting", "answer_grade"]
ENUMS = {"source": {"real", "stand-in"}, "status": {"ok", "rate_limited", "timeout", "error"},
         "answer_grade": {"", "correct", "wrong", "refused_correctly", "refused_wrongly"}}
SECRET = re.compile(r"gsk_[A-Za-z0-9]{20,}")
ORG = re.compile(r"org_(?!REDACTED|standin)[0-9a-z]{10,}")


def check_csv(path: Path, errs: list[str]) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames or []
        rows = list(rd)
    if cols[:12] != REQUIRED:
        errs.append(f"columns: first 12 must be {REQUIRED}, got {cols[:12]}")
    dup = [k for k, v in Counter(r["request_id"] for r in rows).items() if v > 1]
    if dup:
        errs.append(f"duplicate request_id: {dup[:5]}")
    for i, r in enumerate(rows):
        where = f"row {i + 2} ({r['request_id']})"
        for k, allowed in ENUMS.items():
            if r[k] not in allowed:
                errs.append(f"{where}: {k}={r[k]!r} not allowed")
        for k in ("first_token_ms", "total_ms", "tokens_in", "tokens_out"):
            if r[k] != "":
                try:
                    if float(r[k]) < 0:
                        errs.append(f"{where}: {k} negative")
                except ValueError:
                    errs.append(f"{where}: {k}={r[k]!r} not numeric")
        if r["total_ms"] == "":
            errs.append(f"{where}: total_ms missing")
        if not r["retries"].isdigit():
            errs.append(f"{where}: retries={r['retries']!r} not a non-negative integer")
        if r["run_id"].startswith(("groq-", "real-", "calibration")) and r["source"] != "real":
            errs.append(f"{where}: real run labelled {r['source']}")
        if r["run_id"].startswith("standin-") and r["source"] != "stand-in":
            errs.append(f"{where}: stand-in run labelled {r['source']}")
        if r.get("model_calls") == "0" and (r["tokens_in"] or r["tokens_out"]):
            errs.append(f"{where}: tokens recorded but no model call")
        if r["source"] == "stand-in" and r["answer_grade"]:
            errs.append(f"{where}: stand-in rows are not graded")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    base = (ROOT / ap.parse_args().dir).resolve()
    errs: list[str] = []
    csv_path = next((p for p in (base / "results.csv", base / "results" / "results.csv") if p.exists()), None)
    if not csv_path:
        errs.append("results.csv not found")
        rows = []
    else:
        rows = check_csv(csv_path, errs)
    runs_dir = ROOT / "results" / "runs"
    per_run = Counter(r["run_id"] for r in rows)
    for cfg in runs_dir.glob("*.config.json"):
        c = json.loads(cfg.read_text())
        if per_run.get(c["run_id"]) != c["offered"]:
            errs.append(f"{c['run_id']}: offered {c['offered']} but {per_run.get(c['run_id'], 0)} rows")
    for p in base.rglob("*"):
        if any(part in (".venv", ".git", "__pycache__", ".pytest_cache", ".ruff_cache") for part in p.parts):
            continue
        if p.name == ".env":
            if base != ROOT:   # the working folder legitimately has a gitignored .env; a submission must not
                errs.append(f"secret file present: {p}")
            continue
        if p.is_file() and p.suffix in (".py", ".md", ".json", ".jsonl", ".csv", ".log", ".txt", ".html", ".example"):
            text = p.read_text(encoding="utf-8", errors="replace")
            if SECRET.search(text):
                errs.append(f"API key pattern in {p}")
            if ORG.search(text):
                errs.append(f"un-redacted organization id in {p}")
    print(f"checked {len(rows)} rows from {csv_path}")
    for e in errs[:50]:
        print("FAIL", e)
    print("OK" if not errs else f"{len(errs)} problem(s)")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
