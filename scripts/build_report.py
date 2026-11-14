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
| Requests per minute | 30 | 429 body in a 34-request burst ("RPM: Limit 30, Used 30"); 31 of 34 succeeded | error-observed + documented | high |
| Tokens per minute | 8,000 | `x-ratelimit-limit-tokens: 8000`; 2 TPM 429s | header + error-observed + documented | high |
| Requests per day | 1,000 | `x-ratelimit-limit-requests: 1000`; falls 1 per request, not on a 429 | header-observed + documented; not reached | high (value) |
| Tokens per day | 200,000 | Reached in the benchmark: 429 body "TPD: Limit 200000, Used 199068" | error-observed + documented (no header) | high |
| Requests in flight at once | at least 34 | 34 simultaneous requests all accepted; nothing documented | inferred lower bound | low |
| What it returns when a limit is hit | HTTP 429 in 57-359 ms; JSON with limit name, Limit / Used / Requested, "try again in Xs"; `retry-after` (s) | 6 calibration 429s logged with headers and bodies | error-observed | high |
| Time until it recovers | Continuous refill: RPM 1 request / 2 s (recovered 2.5 s after the burst); TPM 133 tokens/s; TPD 2.31 tokens/s (7 min 19 s for a 1,017-token deficit) | (used + requested - limit) / refill rate equals the stated wait for all 6 429s | header + error-observed (arithmetic) | high (RPM, TPM); medium (TPD, one 429) |
"""


# Forces the PDF renderer to start a page here so a table and its caption stay together (layout only).
PAGE_BREAK = '<div style="page-break-before: always"></div>\n\n'


def section(md: str, title: str) -> str:
    m = re.search(rf"## {re.escape(title)}\n\n(.*?)(?=\n## |\Z)", md, re.S)
    return m.group(1).strip() if m else "not measured"


def no_retry_rows(table: str) -> str:
    """The 90/min retry-policy runs stay in results/summaries/tables.md; the report's Table 2 keeps the load scenarios
    (section 6 argues from the retry sweep table instead). Saves space without dropping evidence."""
    return "\n".join(r for r in table.splitlines() if not r.startswith("| Retry:"))


def main():
    src = (ROOT / "docs" / "report_source.md").read_text(encoding="utf-8")
    tables = (ROOT / "results" / "summaries" / "tables.md").read_text(encoding="utf-8")
    sweep_note = (ROOT / "results" / "summaries" / "retry_sentence.md")
    out = (src.replace("{{TABLE_1}}", TABLE_1)
           .replace("{{TABLE_2A}}", "**Table 2a: Latency**\n\n" + no_retry_rows(section(tables, "Table 2a")))
           .replace("{{TABLE_2B}}", PAGE_BREAK + "**Table 2b: Outcomes**\n\n" + no_retry_rows(section(tables, "Table 2b")))
           .replace("{{TABLE_3}}", "**Table 3: Retrieval settings (real Groq)**\n\n" + section(tables, "Table 3"))
           .replace("{{TABLE_SWEEP}}", PAGE_BREAK + "**Retry sweep (stand-in)**\n\n" + section(tables, "Retry sweep (stand-in)"))
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
