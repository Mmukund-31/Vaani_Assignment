"""Render report.pdf and assemble The_Relay_Submission/.

  python -m scripts.package_submission

Excludes: .env, .venv, caches, the downloaded PDFs and their extracted full text (papers/manifest.json records
exact versions, URLs and SHA-256; `python -m relay.ingest` re-downloads and verifies them).
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import markdown
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "The_Relay_Submission"

CSS = """
body { font-family: sans-serif; font-size: 8.6pt; line-height: 1.3; }
h1 { font-size: 14pt; margin: 0 0 4pt 0; } h2 { font-size: 10.5pt; margin: 9pt 0 3pt 0; }
p { margin: 0 0 4pt 0; } table { border-collapse: collapse; margin: 3pt 0 6pt 0; }
td, th { border: 0.5pt solid #999; padding: 1.5pt 3pt; font-size: 7pt; vertical-align: top; }
th { background-color: #eee; } code { font-family: monospace; font-size: 7.8pt; }
"""


def render_pdf(md_path: Path, pdf_path: Path) -> int:
    html = markdown.markdown(md_path.read_text(encoding="utf-8"), extensions=["tables"])
    story = pymupdf.Story(html=html, user_css=CSS)
    writer = pymupdf.DocumentWriter(str(pdf_path))
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (40, 40, -40, -40)
    pages = 0
    more = True
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        pages += 1
    writer.close()
    return pages


def copy(src: str, dst: str | None = None) -> None:
    s = ROOT / src
    d = OUT / (dst or src)
    d.parent.mkdir(parents=True, exist_ok=True)
    if s.is_dir():
        shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".standin.lock"))
    elif s.exists():
        shutil.copy2(s, d)


def main():
    pages = render_pdf(ROOT / "report.md", ROOT / "report.pdf")
    # Build into a fresh folder, then swap: OneDrive sync can lock files in a folder being replaced, and a
    # half-deleted old package must never be mistaken for the new one.
    global OUT
    final = OUT
    OUT = ROOT / f"_build_{time.strftime('%Y%m%dT%H%M%S')}"
    OUT.mkdir()
    for f in ("README.md", "report.md", "report.pdf", "DECISIONS.md", "PROGRESS.md", "PROJECT_PLAN.md",
              "REQUIREMENTS_TRACEABILITY.md"):
        copy(f)
    if (ROOT / "AI_CONVERSATION_HISTORY.md").exists():
        copy("AI_CONVERSATION_HISTORY.md")
    copy("results/results.csv", "results.csv")
    copy("results/groq_calibration.json", "groq_calibration.json")
    # one stage-trace file for all runs in results.csv
    with (OUT / "stage_traces.jsonl").open("w", encoding="utf-8") as out:
        for t in sorted((ROOT / "results" / "traces").glob("*.jsonl")):
            out.write(t.read_text(encoding="utf-8"))
    for d in ("relay", "standin", "experiments", "scripts", "tests"):
        copy(d, f"source_code/{d}")
    for f in ("requirements.txt", "pytest.ini", ".env.example", ".gitignore"):
        copy(f, f"source_code/{f}")
    copy("evaluation")
    copy("docs")
    copy("papers/manifest.json")
    for d in ("runs", "raw", "summaries", "superseded", "retrieval_recall_offline.json"):
        copy(f"results/{d}", f"results/{d}")
    for attempt in range(10):
        try:
            if final.exists():
                shutil.rmtree(final)
            OUT.rename(final)
            break
        except PermissionError:
            time.sleep(1 + attempt)
    else:
        raise SystemExit(f"could not replace {final}; new package left at {OUT}")
    OUT = final
    print(f"report.pdf: {pages} page(s); package at {OUT.relative_to(ROOT)}")
    print(json.dumps(sorted(p.name for p in OUT.iterdir()), indent=0))


if __name__ == "__main__":
    main()
