"""Rebuild everything derived from raw results, in order, and stop at the first failure.

  python -m scripts.finalize

results.csv -> tables -> report.md -> report.pdf + The_Relay_Submission/ -> validation of the package.
Also reports any benchmark answers that still have no grade in evaluation/grades.json.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEPS = ["scripts.build_results", "scripts.make_tables", "scripts.build_report", "scripts.package_submission"]


def main():
    for mod in STEPS:
        print(f"== {mod}", flush=True)
        if subprocess.run([sys.executable, "-m", mod], cwd=ROOT, stdout=subprocess.DEVNULL).returncode:
            raise SystemExit(f"{mod} failed")
    r = subprocess.run([sys.executable, "-m", "scripts.validate_submission", "--dir", "The_Relay_Submission"], cwd=ROOT)
    grades = json.loads((ROOT / "evaluation" / "grades.json").read_text(encoding="utf-8"))
    with (ROOT / "results" / "results.csv").open(encoding="utf-8") as f:
        missing = [row["request_id"] for row in csv.DictReader(f)
                   if row["scenario"].startswith("retrieval_") and not row["scenario"].endswith("_budget_wait")
                   and row["status"] == "ok" and row["request_id"] not in grades]
    print(f"ungraded benchmark answers: {len(missing)}" + (f" e.g. {missing[:3]}" if missing else ""))
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
