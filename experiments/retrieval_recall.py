"""Offline retrieval check (no model calls): does top-k contain a gold page for each answerable question?"""
import json
from pathlib import Path

from relay.retrieval import SETTINGS, build_index

ROOT = Path(__file__).resolve().parent.parent


def gold_hit(q: dict, hits) -> bool:
    got = {(h.chunk.paper, p) for h in hits for p in range(h.chunk.page_start, h.chunk.page_end + 1)}
    gold = set()
    for g in q["gold_pages"]:
        if isinstance(g, str):
            paper, page = g.split(":")
            gold.add((paper, int(page)))
        else:
            gold.add((q["expected_paper"], g))
    if q["category"] == "cross":  # every paper involved must be represented by a gold page
        papers = {p for p, _ in gold}
        return all(any((p, pg) in got for pp, pg in gold if pp == p) for p in papers)
    return bool(gold & got)


def main():
    qs = json.loads((ROOT / "evaluation" / "questions.json").read_text(encoding="utf-8"))
    ans = [q for q in qs if q["answerable"]]
    out = {}
    for name, s in SETTINGS.items():
        ix = build_index(s)
        hits = {q["id"]: ix.search(q["question"]) for q in ans}
        rec = {qid: gold_hit(q, hits[q["id"]]) for q, qid in ((q, q["id"]) for q in ans)}
        ctx = sum(sum(h.chunk.ntok for h in hits[q["id"]]) for q in ans) / len(ans)
        out[name] = {"setting": s.label(), "recall": sum(rec.values()) / len(ans), "misses": [k for k, v in rec.items() if not v],
                     "avg_context_tokens": round(ctx)}
        print(f"{s.label():32s} gold-page recall={out[name]['recall']:.2f} ctx_tok={out[name]['avg_context_tokens']} misses={out[name]['misses']}")
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "retrieval_recall_offline.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
