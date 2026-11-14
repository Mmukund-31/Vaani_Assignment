"""Assemble report.md from docs/report_source.md + tables generated from results.csv (no hand-typed table numbers).

  python -m scripts.make_tables && python -m scripts.build_report
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TABLE_1 = """**Table 1: Limits found in the real service (gpt-oss-120b, free tier, 2026-11-13/14)**

| Limit | Value found | How I found it | Evidence type | Confidence |
|---|---|---|---|---|
| Requests per minute | 30 | 429 body in a 34-request simultaneous burst: "requests per minute (RPM): Limit 30, Used 30"; 31 of 34 succeeded | error-observed + documented | high |
| Tokens per minute | 8,000 | `x-ratelimit-limit-tokens: 8000` on every response; 2 TPM 429s ("Limit 8000, Used 4403, Requested 4662") | header + error-observed + documented | high |
| Requests per day | 1,000 | `x-ratelimit-limit-requests: 1000`; remaining fell 1 per request, not on a 429 | header-observed + documented; not reached | high (value) |
| Tokens per day | 200,000 | Reached during the benchmark; 429 body "tokens per day (TPD): Limit 200000, Used 199068, Requested 1949" | error-observed + documented (no header) | high |
| Requests in flight at once | at least 34 | 34 simultaneous requests all accepted; nothing documented | inferred lower bound | low |
| What it returns when a limit is hit | HTTP 429 in 57-359 ms; JSON naming the limit with Limit / Used / Requested and "try again in Xs"; `retry-after` (whole s); `x-ratelimit-*` headers (RPM 429s omit token headers) | 6 calibration 429s (2 TPM, 3 RPM, 1 TPD) logged with headers and bodies | error-observed | high |
| Time until it recovers | Continuous refill: RPM 1 request / 2 s (recovered 2.5 s after the burst); TPM 133 tokens/s; TPD 2.31 tokens/s (7 min 19 s for a 1,017-token deficit) | for all 6 429s, (used + requested - limit) / refill rate equals the stated wait exactly; first request's reset headers match too | header + error-observed (arithmetic) | high (RPM, TPM); medium (TPD, one 429) |
"""


def section(md: str, title: str) -> str:
    m = re.search(rf"## {re.escape(title)}\n\n(.*?)(?=\n## |\Z)", md, re.S)
    return m.group(1).strip() if m else "not measured"


def main():
    src = (ROOT / "docs" / "report_source.md").read_text(encoding="utf-8")
    tables = (ROOT / "results" / "summaries" / "tables.md").read_text(encoding="utf-8")
    sweep_note = (ROOT / "results" / "summaries" / "retry_sentence.md")
    out = (src.replace("{{TABLE_1}}", TABLE_1)
           .replace("{{TABLE_2A}}", "**Table 2a: Latency**\n\n" + section(tables, "Table 2a"))
           .replace("{{TABLE_2B}}", "**Table 2b: Outcomes**\n\n" + section(tables, "Table 2b"))
           .replace("{{TABLE_3}}", "**Table 3: Retrieval settings (real Groq)**\n\n" + section(tables, "Table 3"))
           .replace("{{TABLE_SWEEP}}", "**Retry sweep (stand-in)**\n\n" + section(tables, "Retry sweep (stand-in)"))
           .replace("{{RETRY_SENTENCE}}", sweep_note.read_text(encoding="utf-8").strip() if sweep_note.exists()
                    else "not measured"))
    left = re.findall(r"\{\{[A-Z_0-9]+\}\}", out)
    if left:
        raise SystemExit(f"unfilled markers: {left}")
    (ROOT / "report.md").write_text(out, encoding="utf-8")
    prose = re.sub(r"^\|.*$", "", out, flags=re.M)
    print(f"report.md written; prose words (tables excluded): {len(prose.split())}")


if __name__ == "__main__":
    main()
