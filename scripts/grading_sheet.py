"""Produce a side-by-side sheet for MANUAL grading of benchmark answers.

  python -m scripts.grading_sheet  -> evaluation/grading_sheet.md

For each answer: question, reference answer, grading notes, the released answer with its citations, whether the
cited pages include a gold page, and a mechanical key-fact check. The mechanical check is only a reading aid;
grades in evaluation/grades.json are assigned by a person reading the answer against evaluation/grading_guidelines.md.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cited_pages(citations: list[str]) -> set[tuple[str, int]]:
    out = set()
    key = {"1706.03762": "attention", "2410.00037": "moshi", "2507.07955": "hnet"}
    for c in citations:
        m = re.search(r"arXiv:(\d{4}\.\d{5})v\d+, pp?\. (\d+)(?:-(\d+))?", c)
        if m:
            a, b = int(m.group(2)), int(m.group(3) or m.group(2))
            out |= {(key[m.group(1)], p) for p in range(a, b + 1)}
    return out


def gold(q: dict) -> set[tuple[str, int]]:
    g = set()
    for x in q["gold_pages"]:
        if isinstance(x, str):
            p, n = x.split(":")
            g.add((p, int(n)))
        else:
            g.add((q["expected_paper"], x))
    return g


def main():
    qs = {q["id"]: q for q in json.loads((ROOT / "evaluation" / "questions.json").read_text(encoding="utf-8"))}
    lines = ["# Grading sheet (generated; grades live in evaluation/grades.json)\n"]
    for f in sorted((ROOT / "results" / "traces").glob("groq-retrieval*.jsonl")):
        lines.append(f"\n## Run {f.stem}\n")
        for line in f.read_text(encoding="utf-8").splitlines():
            t = json.loads(line)
            q = qs[t["question_id"]]
            facts = [(k, bool(re.search(k, t["answer"], re.I))) for k in q["key_facts"]]
            cp = cited_pages(t["citations"])
            lines += [f"### {t['request_id']}  ({q['id']}, answerable={q['answerable']}, decision={t['decision']})",
                      f"**Q:** {q['question']}", f"**Reference:** {q['reference_answer']}",
                      f"**Notes:** {q['grading_notes']}", f"**Answer:** {t['answer']}",
                      f"**Raw model text:** {t['answer_raw'][:600]}",
                      f"**Cited gold page:** {bool(cp & gold(q)) if q['answerable'] else 'n/a'}; "
                      f"key facts: {facts}", ""]
    out = ROOT / "evaluation" / "grading_sheet.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
