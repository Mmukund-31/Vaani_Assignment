"""Section-aware chunking + BM25 retrieval + optional lexical re-rank.

Chunk size unit: tokens counted with tiktoken `cl100k_base`. Groq's Llama models use a
different tokenizer, so this is an approximation (~within 10-15%); the provider-reported
usage is what goes into results.csv.
"""
from __future__ import annotations

import json

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import tiktoken
from rank_bm25 import BM25Okapi

ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "papers" / "processed"
PAPER_KEYS = ["attention", "moshi", "hnet"]
SHORT = {"attention": "Attention Is All You Need", "moshi": "Moshi", "hnet": "Dynamic Chunking (H-Net)"}
REFERENCE_SECTIONS = {"references"}
MIN_CHUNK_TOKENS = 25

_enc = tiktoken.get_encoding("cl100k_base")

STOP = set("""a an the of to in on for and or is are was were be been being by with as at from that this
these those it its which what how why when where who whom does do did can could should would may might
than then there their them they we our us you your i me my not no so such into about over under between
paper papers according describe explain use used using""".split())

# Words that route a query to a paper. Used by the re-ranker and the planner.
PAPER_ALIASES = {
    "attention": ["transformer", "attention is all you need", "vaswani", "wmt", "bleu",
                  "english-to-german", "english-to-french", "constituency"],
    "moshi": ["moshi", "mimi", "helium", "inner monologue", "full-duplex", "kyutai", "rq-transformer",
              "speech-text", "spoken dialogue", "depth transformer"],
    "hnet": ["h-net", "hnet", "dynamic chunking", "hierarchical sequence", "byte-level", "tokenizer-free",
             "routing module", "dechunking", "smoothing module", "ratio loss"],
}


def ntokens(text: str) -> int:
    return len(_enc.encode(text, disallowed_special=()))


def tokenize(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower())
    out = []
    for t in toks:
        if t in STOP:
            continue
        out.append(t)
        if "-" in t:  # index both "h-net" and "h", "net"
            out.extend(p for p in t.split("-") if p and p not in STOP)
    return out


@dataclass
class Chunk:
    chunk_id: str
    paper: str
    arxiv_id: str
    version: str
    title: str
    section: str
    page_start: int
    page_end: int
    text: str
    ntok: int

    def citation(self) -> str:
        pages = f"p. {self.page_start}" if self.page_start == self.page_end else f"pp. {self.page_start}-{self.page_end}"
        return f"[{self.title}, arXiv:{self.arxiv_id}{self.version}, {pages}, Section {self.section}]"


@dataclass(frozen=True)
class RetrievalSetting:
    name: str
    chunk_tokens: int
    top_k: int
    rerank: bool
    overlap_frac: float = 0.15
    exclude_references: bool = True

    def label(self) -> str:
        return f"{self.name}:{self.chunk_tokens}tok/k{self.top_k}/rerank={'yes' if self.rerank else 'no'}"


SETTINGS = {
    "A": RetrievalSetting("A", 300, 3, False),
    "B": RetrievalSetting("B", 600, 4, False),
    "C": RetrievalSetting("C", 600, 4, True),
    "D": RetrievalSetting("D", 900, 5, True),
}


def load_pages(paper: str) -> list[dict]:
    return json.loads((PROCESSED / f"{paper}.json").read_text(encoding="utf-8"))


def _units(text: str) -> list[str]:
    """Split into sentence-ish units so chunks don't cut mid-sentence where avoidable."""
    text = re.sub(r"-\n(?=[a-z])", "", text)  # de-hyphenate line breaks
    text = re.sub(r"\s*\n\s*", " ", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", text)
    return [p.strip() for p in parts if p.strip()]


def chunk_paper(paper: str, chunk_tokens: int, overlap_frac: float) -> list[Chunk]:
    pages = load_pages(paper)
    meta = pages[0]
    # Flatten into (section, page, unit) preserving order; break chunks at section changes.
    stream: list[tuple[str, int, str]] = []
    for pg in pages:
        for seg in pg["segments"]:
            for u in _units(seg["text"]):
                stream.append((seg["section"], pg["page"], u))

    chunks: list[Chunk] = []
    overlap = int(chunk_tokens * overlap_frac)
    i = 0
    while i < len(stream):
        section = stream[i][0]
        buf: list[tuple[str, int, str]] = []
        tok = 0
        j = i
        while j < len(stream) and stream[j][0] == section and (tok < chunk_tokens or not buf):
            t = ntokens(stream[j][2])
            if buf and tok + t > chunk_tokens * 1.15:
                break
            buf.append(stream[j])
            tok += t
            j += 1
        text = " ".join(u for _, _, u in buf)
        if tok >= MIN_CHUNK_TOKENS:  # drop heading-only fragments ("3 Model Architecture" alone)
            chunks.append(Chunk(
                chunk_id=f"{paper}-{chunk_tokens}-{len(chunks):04d}", paper=paper,
                arxiv_id=meta["arxiv_id"], version=meta["version"], title=meta["title"], section=section,
                page_start=buf[0][1], page_end=buf[-1][1], text=text, ntok=tok,
            ))
        if j >= len(stream) or stream[j][0] != section:
            i = j  # new section: no overlap across section boundaries
        else:
            # step back to create ~overlap tokens of overlap
            back, k = 0, j
            while k > i + 1 and back < overlap:
                k -= 1
                back += ntokens(stream[k][2])
            i = k if k > i else j
    return chunks


@dataclass
class Hit:
    chunk: Chunk
    score: float
    bm25: float
    label: str = ""


@dataclass
class Index:
    setting: RetrievalSetting
    chunks: list[Chunk]
    bm25: BM25Okapi = field(repr=False)

    def search(self, query: str, top_k: int | None = None, papers: list[str] | None = None) -> list[Hit]:
        k = top_k or self.setting.top_k
        q = tokenize(query)
        if not q:
            return []
        scores = self.bm25.get_scores(q)
        order = sorted(range(len(scores)), key=lambda n: -scores[n])
        if papers:
            order = [n for n in order if self.chunks[n].paper in papers]
        pool = order[: max(k * 4, 20)] if self.setting.rerank else order[:k]
        hits = [Hit(self.chunks[n], float(scores[n]), float(scores[n])) for n in pool]
        if self.setting.rerank:
            hits = rerank(query, hits)
        hits = hits[:k]
        for n, h in enumerate(hits, 1):
            h.label = f"P{n}"
        return hits


def mentioned_papers(query: str) -> list[str]:
    ql = query.lower()
    return [p for p, names in PAPER_ALIASES.items() if any(n in ql for n in names)]


def rerank(query: str, hits: list[Hit]) -> list[Hit]:
    """Second-stage lexical re-rank (no model call, ~0 ms):
    - normalised BM25
    - query-bigram phrase matches (penalises bag-of-words coincidences)
    - query-term coverage (fraction of distinct query terms present)
    - paper routing: if the query names a paper, boost chunks from that paper
    - section-title match: query terms appearing in the section heading
    """
    if not hits:
        return hits
    q = tokenize(query)
    qset = set(q)
    bigrams = {f"{a} {b}" for a, b in zip(q, q[1:])}
    routed = mentioned_papers(query)
    top = max(h.bm25 for h in hits) or 1.0
    for h in hits:
        toks = tokenize(h.chunk.text)
        tset = set(toks)
        tb = {f"{a} {b}" for a, b in zip(toks, toks[1:])}
        coverage = len(qset & tset) / max(1, len(qset))
        phrase = len(bigrams & tb) / max(1, len(bigrams)) if bigrams else 0.0
        sec = len(qset & set(tokenize(h.chunk.section))) / max(1, len(qset))
        route = 0.0
        if routed:
            route = 0.35 if h.chunk.paper in routed else -0.35
        h.score = 0.45 * (h.bm25 / top) + 0.25 * coverage + 0.2 * phrase + 0.1 * sec + route
    return sorted(hits, key=lambda h: -h.score)


@lru_cache(maxsize=8)
def build_index(setting: RetrievalSetting) -> Index:
    chunks: list[Chunk] = []
    for p in PAPER_KEYS:
        for c in chunk_paper(p, setting.chunk_tokens, setting.overlap_frac):
            if setting.exclude_references and c.section.lower() in REFERENCE_SECTIONS:
                continue
            chunks.append(c)
    bm25 = BM25Okapi([tokenize(c.section + " " + c.text) for c in chunks])
    return Index(setting, chunks, bm25)


def format_passages(hits: list[Hit]) -> str:
    out = []
    for h in hits:
        c = h.chunk
        pages = f"p. {c.page_start}" if c.page_start == c.page_end else f"pp. {c.page_start}-{c.page_end}"
        out.append(f"[{h.label}] ({SHORT[c.paper]}, {pages}, Section {c.section})\n{c.text}")
    return "\n\n".join(out)


