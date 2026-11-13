"""Compute Tables 2a, 2b and 3 from results/results.csv ONLY (so reviewers can recompute them).

  python -m scripts.make_tables            -> prints markdown, writes results/summaries/tables.md

Conventions (also stated in the report):
- Percentiles: linear interpolation (numpy default) over requests with status=ok that released text
  (first_token_ms present). Failed / rejected requests are NOT latency samples; they appear in Table 2b.
- first_token_ms = arrival -> first validated sentence shown to the person (includes queue + limiter waits).
- Outcome % denominators = all offered requests in the scenario; ok + rate_limited + timeout + error = 100.
- Tokens per answer = mean(tokens_in + tokens_out) over ok rows that called the model.
- Cost per 1,000 answers = list-price equivalent at Groq's published gpt-oss-120b price ($0.15 / 1M input,
  $0.60 / 1M output); the free tier actually charges $0. Stand-in rows use the same formula on stand-in counts.
- Offered rate = (n - 1) / (last offered_at - first offered_at) x 60; duration = first arrival to last completion.
- Peak queue = max(queue_depth).
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "results" / "results.csv"
PRICE_IN, PRICE_OUT = 0.15 / 1e6, 0.60 / 1e6

LOAD_ROWS = [  # (label in table, scenario name, source)
    ("Steady (real Groq)", "steady", "real"),
    ("Steady", "steady", "stand-in"),
    ("Sudden burst", "burst", "stand-in"),
    ("Sustained overload", "overload", "stand-in"),
    ("Per-minute limit halved", "rpm_halved", "stand-in"),
    ("Daily budget exhausted", "daily_exhausted", "stand-in"),
    ("Retry: immediate", "retry_immediate", "stand-in"),
    ("Retry: backoff+jitter", "retry_backoff", "stand-in"),
]


def load() -> list[dict]:
    with CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fnum(x):
    return float(x) if x not in ("", None) else None


def pcts(xs, ps):
    xs = [x for x in xs if x is not None]
    if not xs:
        return ["not measured"] * len(ps)
    return [f"{np.percentile(xs, p):.0f}" for p in ps]


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def latency_cells(rows, ps):
    ok = [r for r in rows if r["status"] == "ok" and r["first_token_ms"]]
    return " / ".join(pcts([fnum(r["first_token_ms"]) for r in ok], ps)), \
        " / ".join(pcts([fnum(r["total_ms"]) for r in ok], ps)), len(ok)


def tokens_per_answer(rows):
    ans = [r for r in rows if r["status"] == "ok" and r["tokens_in"] not in ("", None)]
    if not ans:
        return None, None
    tpa = np.mean([fnum(r["tokens_in"]) + fnum(r["tokens_out"]) for r in ans])
    cost = 1000 * np.mean([fnum(r["tokens_in"]) * PRICE_IN + fnum(r["tokens_out"]) * PRICE_OUT for r in ans])
    return tpa, cost


def table2(all_rows):
    a = ["| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | "
         "First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) | Latency samples (n) |",
         "|---|---|---|---|---|---|---|---|"]
    b = ["| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Error % | Retries per request | "
         "Peak queue length | Tokens per answer | Cost per 1,000 answers (list-price equiv.) |",
         "|---|---|---|---|---|---|---|---|---|"]
    for label, scen, src in LOAD_ROWS:
        rows = [r for r in all_rows if r["scenario"] == scen and r["source"] == src]
        if not rows:
            a.append(f"| {label} | {src} | not measured | | | | | |")
            b.append(f"| {label} | not measured | | | | | | | |")
            continue
        n = len(rows)
        offs = sorted(fnum(r["offered_at_s"]) for r in rows if r["offered_at_s"])
        rate = (len(offs) - 1) / (offs[-1] - offs[0]) * 60 if len(offs) > 1 and offs[-1] > offs[0] else float("nan")
        start = min(ts(r["start_time"]) for r in rows)
        end = max(ts(r["start_time"]) + fnum(r["total_ms"]) / 1000 for r in rows)
        ft, tt, k = latency_cells(rows, [50, 95, 99])
        a.append(f"| {label} | {src} | {n} | {end - start:.0f} | {rate:.1f} | {ft} | {tt} | {k} |")
        st = {s: 100 * sum(r["status"] == s for r in rows) / n for s in ("ok", "rate_limited", "timeout", "error")}
        retries = np.mean([int(r["retries"]) for r in rows])
        peak = max(int(r["queue_depth"] or 0) for r in rows)
        tpa, cost = tokens_per_answer(rows)
        b.append(f"| {label} | {st['ok']:.1f} | {st['rate_limited']:.1f} | {st['timeout']:.1f} | {st['error']:.1f} | "
                 f"{retries:.2f} | {peak} | {tpa:.0f} | ${cost:.3f} |" if tpa is not None else
                 f"| {label} | {st['ok']:.1f} | {st['rate_limited']:.1f} | {st['timeout']:.1f} | {st['error']:.1f} | "
                 f"{retries:.2f} | {peak} | not measured | not measured |")
    return "\n".join(a), "\n".join(b)


def table3(all_rows):
    out = ["| Setting (piece size, pieces fetched, re-rank) | Questions asked | Correct and cited % | Wrong % | "
           "Correctly refused % | Refused wrongly % | Tokens per answer | First token p50 / p95 (ms) | "
           "Total time p50 / p95 (ms) |", "|---|---|---|---|---|---|---|---|---|"]
    settings = sorted({r["scenario"] for r in all_rows if r["scenario"].startswith("retrieval_")})
    for scen in settings:
        rows = [r for r in all_rows if r["scenario"] == scen and r["source"] == "real"]
        if not rows:
            continue
        n = len(rows)
        g = {k: 100 * sum(r["answer_grade"] == k for r in rows) / n
             for k in ("correct", "wrong", "refused_correctly", "refused_wrongly")}
        ungraded = sum(not r["answer_grade"] for r in rows)
        tpa, _ = tokens_per_answer(rows)
        ft, tt, _ = latency_cells(rows, [50, 95])
        label = rows[0]["retrieval_setting"].split(":", 1)[-1]
        note = f" ({ungraded} ungraded)" if ungraded else ""
        out.append(f"| {scen[-1]}: {label} | {n}{note} | {g['correct']:.1f} | {g['wrong']:.1f} | "
                   f"{g['refused_correctly']:.1f} | {g['refused_wrongly']:.1f} | {tpa:.0f} | {ft} | {tt} |")
    return "\n".join(out)


def main():
    rows = load()
    t2a, t2b = table2(rows)
    t3 = table3(rows)
    md = f"## Table 2a\n\n{t2a}\n\n## Table 2b\n\n{t2b}\n\n## Table 3\n\n{t3}\n"
    out = ROOT / "results" / "summaries" / "tables.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
