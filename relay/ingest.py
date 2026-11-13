"""Download the three pinned arXiv papers, verify them, and extract per-page text.

Run: python -m relay.ingest

Page convention: `page` is the 1-based PDF page index (what a PDF viewer shows),
not the printed page number in the paper's footer.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
import httpx

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "papers" / "raw"
PROCESSED = ROOT / "papers" / "processed"
MANIFEST = ROOT / "papers" / "manifest.json"

PAPERS = [
    {"key": "attention", "arxiv_id": "1706.03762", "version": "v7",
     "title": "Attention Is All You Need", "short": "Attention"},
    {"key": "moshi", "arxiv_id": "2410.00037", "version": "v1",
     "title": "Moshi: a speech-text foundation model for real-time dialogue", "short": "Moshi"},
    {"key": "hnet", "arxiv_id": "2507.07955", "version": "v2",
     "title": "Dynamic Chunking for End-to-End Hierarchical Sequence Modeling", "short": "H-Net"},
]

# Numbered section headings like "3.2 Attention", "A.1 Foo", "4 Experiments"; and a few unnumbered ones.
HEADING_RE = re.compile(r"^(?:(?:[A-H]|\d{1,2})(?:\.\d{1,2}){0,3}\.?)\s+[A-Z][A-Za-z0-9 ,:\-–()/&'’]{2,80}$")
UNNUMBERED = {"abstract", "introduction", "conclusion", "conclusions", "references",
              "acknowledgements", "acknowledgments", "related work", "appendix",
              "attention visualizations"}


def pdf_url(p: dict) -> str:
    return f"https://arxiv.org/pdf/{p['arxiv_id']}{p['version']}"


def download(p: dict, client: httpx.Client) -> Path:
    path = RAW / f"{p['arxiv_id']}{p['version']}.pdf"
    if path.exists() and path.stat().st_size > 10_000:
        return path
    r = client.get(pdf_url(p), follow_redirects=True, timeout=120)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise RuntimeError(f"{pdf_url(p)} did not return a PDF")
    path.write_bytes(r.content)
    return path


def verify_abs_page(p: dict, client: httpx.Client) -> dict:
    """Confirm the requested version exists on arXiv's abstract page and the title matches."""
    url = f"https://arxiv.org/abs/{p['arxiv_id']}{p['version']}"
    r = client.get(url, follow_redirects=True, timeout=60)
    r.raise_for_status()
    html = r.text
    m = re.search(r'<meta name="citation_title" content="([^"]+)"', html)
    abs_title = m.group(1) if m else ""
    norm = lambda s: re.sub(r"\W+", " ", s).strip().lower()  # noqa: E731
    return {
        "abs_url": url,
        "abs_title": abs_title,
        "title_matches": norm(abs_title) == norm(p["title"]),
        "version_listed": f"[{p['version']}]" in html,
    }


NUM_ONLY_RE = re.compile(r"^(?:[A-H]|\d{1,2})(?:\.\d{1,2}){0,3}\.?$")


def is_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 90:
        return False
    if s.lower().rstrip(".") in UNNUMBERED:
        return not s.endswith(".")  # "Related Work." is a run-in paragraph label, not a section
    if re.match(r"^Appendix [A-Z]\.?\s+[A-Z]", s):
        return True
    return bool(HEADING_RE.match(s)) and not s.endswith(".") and sum(c.isdigit() for c in s) < 8


def page_lines(page) -> list[tuple[str, bool]]:
    """(text, is_bold) for each visual line on the page."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            text = "".join(sp["text"] for sp in ln["spans"]).strip()
            spans = [sp for sp in ln["spans"] if sp["text"].strip()]
            if not spans:
                continue
            bold = all((sp["flags"] & 16) or "Bold" in sp["font"] or sp["font"].endswith("TB")
                       or "CSC" in sp["font"] for sp in spans)
            out.append((text, bold))
    return out


def segment_page(lines: list[tuple[str, bool]], current: str) -> tuple[list[dict], list[str], str]:
    """Split a page's lines into segments, each tagged with the section it belongs to."""
    segments: list[dict] = [{"section": current, "lines": []}]
    heads: list[str] = []
    i = 0
    while i < len(lines):
        text, bold = lines[i]
        head = None
        step = 1
        if bold and NUM_ONLY_RE.match(text) and i + 1 < len(lines) and lines[i + 1][1]:
            cand = f"{text.rstrip('.')} {lines[i + 1][0]}"
            if is_heading(cand):
                head, step = cand, 2
        if head is None and bold and is_heading(text):
            head = text.rstrip(".")
        if head:
            heads.append(head)
            current = head
            segments.append({"section": head, "lines": [head]})
        else:
            segments[-1]["lines"].append(text)
        i += step
    out = [{"section": sg["section"], "text": "\n".join(sg["lines"])} for sg in segments if sg["lines"]]
    return out, heads, current


def extract_pages(path: Path, p: dict) -> list[dict]:
    doc = pymupdf.open(path)
    pages = []
    current = "Front matter"
    for i, page in enumerate(doc, start=1):
        start = current
        segments, heads, current = segment_page(page_lines(page), current)
        pages.append({
            "paper": p["key"], "arxiv_id": p["arxiv_id"], "version": p["version"],
            "title": p["title"], "page": i, "section_at_start": start,
            "headings": heads, "segments": segments,
            "text": "\n".join(sg["text"] for sg in segments),
        })
    return pages


def first_page_has_title(pages: list[dict], p: dict) -> bool:
    head = re.sub(r"\s+", " ", pages[0]["text"]).lower()
    probe = re.sub(r"\s+", " ", p["title"].split(":")[0]).lower()
    return probe in head


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    manifest = {"page_convention": "1-based PDF page index", "papers": []}
    ok = True
    with httpx.Client(headers={"User-Agent": "relay-ingest/0.1"}) as client:
        for p in PAPERS:
            path = download(p, client)
            data = path.read_bytes()
            absinfo = verify_abs_page(p, client)
            pages = extract_pages(path, p)
            title_in_pdf = first_page_has_title(pages, p)
            entry = {
                **p, "pdf_url": pdf_url(p), "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
                "num_pages": len(pages), "title_in_pdf_page1": title_in_pdf,
                "ingested_at": datetime.now(timezone.utc).isoformat(), **absinfo,
            }
            manifest["papers"].append(entry)
            (PROCESSED / f"{p['key']}.json").write_text(json.dumps(pages, indent=1), encoding="utf-8")
            good = absinfo["title_matches"] and absinfo["version_listed"] and title_in_pdf
            ok &= good
            print(f"{p['arxiv_id']}{p['version']}: pages={len(pages)} sha256={entry['sha256'][:12]} "
                  f"abs_title_ok={absinfo['title_matches']} version_listed={absinfo['version_listed']} "
                  f"title_in_pdf={title_in_pdf}")
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if not ok:
        print("VERIFICATION FAILED - see manifest", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
