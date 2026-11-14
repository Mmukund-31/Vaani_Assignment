"""Recompute Tables 2a/2b/3 from results.csv with the csv module (independent of make_tables.py) and compare with
results/summaries/tables.md cell by cell.

  python -m scripts.crosscheck_tables
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
LOAD = [("steady", "real"), ("steady", "stand-in"), ("burst", "stand-in"), ("overload", "stand-in"),
        ("rpm_halved", "stand-in"), ("daily_exhausted", "stand-in"), ("retry_none", "stand-in"),
        ("retry_immediate", "stand-in"), ("retry_backoff", "stand-in")]


def pct(xs: list[float], p: float) -> str:
    return f"{np.percentile(xs, p):.0f}"


def num(x: str):
    return float(x) if x != "" else None


def main():
    with (ROOT / "results" / "results.csv").open(encoding="utf-8") as f:
        df = list(csv.DictReader(f))
    tables = (ROOT / "results" / "summaries" / "tables.md").read_text(encoding="utf-8")
    blob = "\n".join(r for r in tables.splitlines() if r.startswith("| "))
    expect = []
    for scen, src in LOAD:
        d = [r for r in df if r["scenario"] == scen and r["source"] == src]
        n = len(d)
        ok = [num(r["first_token_ms"]) for r in d if r["status"] == "ok" and r["first_token_ms"] != ""]
        ft = " / ".join(pct(ok, p) for p in (50, 95, 99))
        row = next((r for r in blob.splitlines() if f"| {ft} |" in r), "")
        expect.append(ft if f"| {n} |" in row else f"{ft} (row with requests={n})")
        rl = 100 * sum(r["status"] == "rate_limited" for r in d) / n
        p429 = 100 * sum(r["status"] == "rate_limited" and r["error_type"].startswith("provider_429") for r in d) / n
        okp = 100 * sum(r["status"] == "ok" for r in d) / n
        expect.append(f"{okp:.1f} | {rl:.1f} ({p429:.1f} / {rl - p429:.1f})")
    qs = json.loads((ROOT / "evaluation" / "questions.json").read_text(encoding="utf-8"))
    for s in "ABCD":
        d = [r for r in df if r["scenario"] == f"retrieval_{s}" and r["source"] == "real"]
        n = len(d)
        g = {k: sum(r["answer_grade"] == k for r in d) for k in ("correct", "wrong", "refused_correctly", "refused_wrongly")}
        expect.append(f"| {n} | {100 * g['correct'] / n:.1f} | {100 * g['wrong'] / n:.1f} | "
                      f"{100 * g['refused_correctly'] / n:.1f} | {100 * g['refused_wrongly'] / n:.1f}")
        ans = [num(r["tokens_in"]) + num(r["tokens_out"]) for r in d if r["status"] == "ok" and r["tokens_in"] != ""]
        ft = [num(r["first_token_ms"]) for r in d if r["first_token_ms"] != ""]
        expect.append(f"{np.mean(ans):.0f} | " + " / ".join(pct(ft, p) for p in (50, 95)))
        assert len({r["question_id"] for r in d}) == n == len(qs), f"setting {s}: not one final row per question"
    assert not any(r["answer_grade"] for r in df if r["source"] == "stand-in"), "stand-in rows must be ungraded"
    missing = [e for e in expect if e not in blob]
    print(f"checked {len(expect)} recomputed cell groups against tables.md; mismatches: {len(missing)}")
    for m in missing:
        print("  NOT FOUND:", m)
    print("stand-in rows ungraded: ok; one final row per question in A-D: ok")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
