"""The Relay pipeline.

Stages
 1. intake      - validate the question, assign id + deadline
 2. retrieve    - TOOL: BM25 search over the three papers (search_papers)
 3. plan        - BRANCH: (a) refuse without a model call if there is no lexical evidence,
                  (b) LOOP (bounded, 1 extra round): per-paper retrieval for comparison questions or when a
                  named paper is missing from the hits, (c) otherwise generate
 4. generate    - one streamed model call; passages labelled [P1..Pk]; the model cites labels only
 5. validate    - deterministic, *per sentence while streaming*: every sentence must cite a retrieved label and
                  every number it states must appear in a cited passage. Only validated sentences are released
                  to the user, so streaming never shows unverified text. If the first sentence fails we
                  regenerate once with feedback; if a later one fails we stop and say the rest was withheld.

Time to first token (TTFT) reported by this module = arrival -> first *validated* sentence released to the
user (what the person experiences). The provider's raw TTFT is kept separately as model_ttft_ms.
"""
from __future__ import annotations

import asyncio
import math
import random
import re
import time
import uuid
from dataclasses import dataclass, field

from .limits import Governor, LimitReject
from .providers import Completion, Provider, ProviderError, ProviderTimeout, RateLimited
from .retrieval import Hit, Index, PAPER_KEYS, SHORT, format_passages, mentioned_papers, ntokens

REFUSAL_TAG = "NOT_IN_LIBRARY"
MIN_TOP_BM25 = 4.0       # below this the library has no lexical support for the question at all

SYSTEM_PROMPT = f"""You answer engineers' questions using ONLY the numbered passages provided from three papers:
"Attention Is All You Need" (arXiv:1706.03762v7), "Moshi" (arXiv:2410.00037v1) and
"Dynamic Chunking for End-to-End Hierarchical Sequence Modeling" (H-Net, arXiv:2507.07955v2).

Rules:
- Use only facts stated in the passages. Do not use outside knowledge, even if you are sure.
- End EVERY sentence with the label(s) of the passage(s) that support it, e.g. "... gradients [P2]." or "[P1][P3]".
- Copy numbers exactly as written in the passages. Do not compute new numbers.
- Pay attention to which paper each passage comes from; do not attribute one paper's result to another.
- If the passages do not contain the answer, reply with a single line starting with "{REFUSAL_TAG}:" followed
  by one short sentence saying what the papers do not cover. Do not guess.
- Be concise: at most 5 sentences. No preamble, no headings."""

COMPARE_RE = re.compile(r"\b(compare|compared|comparison|both|each of|versus|vs\.?|differ|difference|all three)\b", re.I)
CITE_RE = re.compile(r"\[(P\d+(?:\s*[,;]\s*P\d+)*)\]")
NUM_RE = re.compile(r"(?<![A-Za-z\[])(\d+(?:[.,]\d+)*)")
WITHHELD = "(The rest of this answer was withheld because it could not be verified against the papers.)"
CANNOT_VERIFY = "I could not produce an answer I could verify against the papers, so I am not giving one."


# ---------------------------------------------------------------- retry policy
@dataclass
class RetryPolicy:
    mode: str = "backoff"      # 'backoff' (exp + full jitter, honours Retry-After) | 'immediate' | 'none'
    max_retries: int = 2
    base_s: float = 0.5
    cap_s: float = 8.0
    rng: random.Random = field(default_factory=lambda: random.Random(7))

    def wait_for(self, attempt: int, err: ProviderError) -> float:
        if self.mode == "immediate":
            return 0.0
        backoff = self.rng.uniform(0, min(self.cap_s, self.base_s * (2 ** attempt)))
        return max(backoff, err.retry_after or 0.0)


# ---------------------------------------------------------------- request record
@dataclass
class RequestRecord:
    request_id: str
    question: str
    arrival: float                        # monotonic
    deadline: float                       # monotonic
    start_time: str = ""                  # UTC ISO
    status: str = "ok"                    # ok | rate_limited | timeout | error
    error_type: str = ""
    decision: str = ""                    # answer | refuse_no_evidence | refuse_model | refuse_unverifiable | ...
    answer: str = ""
    answer_raw: str = ""
    citations: list[str] = field(default_factory=list)
    first_token_ms: float | None = None
    total_ms: float | None = None
    model_ttft_ms: list[float] = field(default_factory=list)
    queue_wait_ms: float = 0.0
    limiter_wait_ms: float = 0.0
    retry_wait_ms: float = 0.0
    retrieval_ms: float = 0.0
    model_ms: float = 0.0
    retries: int = 0
    model_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    reasoning_tokens: int = 0
    retrieval_rounds: int = 0
    retrieved: list[str] = field(default_factory=list)
    http_status: int | None = None
    degraded: bool = False
    setting_used: str = ""
    queue_depth: int = 0                  # backlog at admission: slot queue + governor waiters (incl. this one if queued)
    sentences_released: int = 0
    sentences_rejected: int = 0
    events: list[tuple[str, float]] = field(default_factory=list)
    released: list[str] = field(default_factory=list)

    def ev(self, name: str) -> None:
        self.events.append((name, round((time.monotonic() - self.arrival) * 1000, 1)))

    @property
    def working_ms(self) -> float:
        return self.retrieval_ms + self.model_ms


# ---------------------------------------------------------------- validation helpers
_NORMALISE = str.maketrans({"【": "[", "】": "]", "［": "[", "］": "]",
                            "​": None, "‌": None, "‍": None, "﻿": None, "⁠": None})


def normalise_model_text(text: str) -> str:
    """gpt-oss sometimes cites with full-width brackets (【P1】) or puts zero-width spaces inside [P1].
    Measured in the first benchmark run: both made correct, cited sentences fail validation."""
    return text.translate(_NORMALISE)


def _norm_num(s: str) -> str:
    return s.replace(",", "").rstrip(".")


def passage_numbers(text: str) -> set[str]:
    return {_norm_num(n) for n in NUM_RE.findall(text)}


def validate_sentence(sentence: str, label_to_hit: dict[str, Hit], question: str) -> str | None:
    """Return None if OK, else a reason string."""
    labels = [lab.strip() for grp in CITE_RE.findall(sentence) for lab in re.split(r"[,;]", grp)]
    if not labels:
        return "no citation"
    bad = [lab for lab in labels if lab not in label_to_hit]
    if bad:
        return f"cites unknown passage {bad}"
    body = CITE_RE.sub("", sentence)
    cited_text = " ".join(label_to_hit[lab].chunk.text for lab in labels)
    allowed = passage_numbers(cited_text) | passage_numbers(question)
    for n in NUM_RE.findall(body):
        nn = _norm_num(n)
        if nn not in allowed and not (nn.isdigit() and int(nn) <= 3):
            return f"number {n} not in cited passages"
    return None


class SentenceGate:
    """Accumulates streamed text and yields complete sentences (with their trailing citations)."""
    END_RE = re.compile(r"[.!?](?:\s*\[P\d+(?:\s*[,;]\s*P\d+)*\])*(?=\s+[^\s\[])")

    def __init__(self):
        self.buf = ""

    def feed(self, piece: str) -> list[str]:
        self.buf += piece
        out = []
        while True:
            m = self._find_end(self.buf)
            if m is None:
                break
            out.append(self.buf[:m].strip())
            self.buf = self.buf[m:].lstrip()
        return [s for s in out if s]

    def _find_end(self, s: str) -> int | None:
        for m in self.END_RE.finditer(s):
            prev = s[max(0, m.start() - 4):m.start() + 1]
            if re.search(r"\d\.$", prev) and m.end() < len(s) and s[m.end():m.end() + 2].strip()[:1].isdigit():
                continue  # decimal number, not a sentence end
            if re.search(r"\b(e\.g|i\.e|et al|Fig|Eq|Sec|vs)\.$", s[:m.start() + 1]):
                continue
            return m.end()
        return None

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


def render(sentence: str, label_to_hit: dict[str, Hit]) -> tuple[str, list[str]]:
    cites = []

    def sub(m):
        out = []
        for lab in re.split(r"\s*[,;]\s*", m.group(1)):
            c = label_to_hit[lab].chunk.citation()
            cites.append(c)
            out.append(c)
        return " ".join(out)
    return CITE_RE.sub(sub, sentence), cites


# ---------------------------------------------------------------- the pipeline
class Pipeline:
    def __init__(self, provider: Provider, index: Index, governor: Governor | None = None,
                 retry: RetryPolicy | None = None, max_output_tokens: int = 400,
                 per_call_first_token_timeout: float = 15.0, allow_regenerate: bool = True,
                 fallback_index: Index | None = None, degrade_after_s: float = 3.0):
        self.provider = provider
        self.index = index
        self.governor = governor
        self.retry = retry or RetryPolicy()
        self.max_output_tokens = max_output_tokens
        self.first_token_timeout = per_call_first_token_timeout
        self.allow_regenerate = allow_regenerate
        self.fallback_index = fallback_index   # smaller-context retrieval used when tokens are scarce
        self.degrade_after_s = degrade_after_s

    # ---- stage 1
    @staticmethod
    def intake(question: str, deadline_s: float, request_id: str | None = None) -> RequestRecord:
        now = time.monotonic()
        rec = RequestRecord(request_id or uuid.uuid4().hex[:12], question.strip(), now, now + deadline_s,
                            start_time=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time()*1000)%1000:03d}Z")
        rec.ev("arrival")
        return rec

    # ---- stages 2+3
    def search_papers(self, query: str, top_k: int | None = None, papers: list[str] | None = None,
                      index: Index | None = None) -> list[Hit]:
        """The retrieval TOOL."""
        return (index or self.index).search(query, top_k=top_k, papers=papers)

    def prompt_tokens(self, hits: list[Hit], question: str) -> int:
        return ntokens(SYSTEM_PROMPT) + ntokens(format_passages(hits)) + ntokens(question) + 20

    def choose_index(self, rec: RequestRecord) -> Index:
        """Degrade (smaller context, fewer tokens) only when the full context would wait too long for TPM and
        the small one would not. The person gets a weaker-but-cited answer instead of a rejection."""
        if not (self.governor and self.fallback_index):
            return self.index
        full = Governor.estimate(self.prompt_tokens(self.index.search(rec.question), rec.question), self.max_output_tokens)
        small = Governor.estimate(self.prompt_tokens(self.fallback_index.search(rec.question), rec.question),
                                  self.max_output_tokens)
        w_full = self.governor.predicted_wait(1, full)
        if w_full <= self.degrade_after_s:
            return self.index
        if self.governor.predicted_wait(1, small) < w_full:
            rec.degraded = True
            rec.ev("degraded_retrieval")
            return self.fallback_index
        return self.index

    def retrieve_and_plan(self, rec: RequestRecord) -> list[Hit]:
        t0 = time.perf_counter()
        rec.ev("retrieve_start")
        index = self.choose_index(rec)
        rec.setting_used = index.setting.name
        k = index.setting.top_k
        hits = self.search_papers(rec.question, index=index)
        rec.retrieval_rounds = 1
        named = mentioned_papers(rec.question)
        comparative = len(named) >= 2 or (COMPARE_RE.search(rec.question) is not None and len(named) != 1)
        hit_papers = {h.chunk.paper for h in hits}
        if comparative or (named and not set(named) <= hit_papers):
            targets = named if named else PAPER_KEYS
            per = max(2, math.ceil(k / len(targets)))
            merged: list[Hit] = []
            for p in targets:
                merged.extend(self.search_papers(rec.question, top_k=per, papers=[p], index=index))
            hits = merged
            for n, h in enumerate(hits, 1):
                h.label = f"P{n}"
            rec.retrieval_rounds = 2
            rec.ev("re_retrieve_per_paper")
        rec.retrieval_ms += (time.perf_counter() - t0) * 1000
        rec.retrieved = [h.chunk.chunk_id for h in hits]
        rec.ev("retrieve_end")
        return hits

    # ---- stage 4+5
    def _messages(self, rec: RequestRecord, hits: list[Hit], feedback: str | None) -> list[dict]:
        user = f"Passages:\n\n{format_passages(hits)}\n\nQuestion: {rec.question}"
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
        if feedback:
            msgs.append({"role": "user", "content": f"Your previous draft was rejected: {feedback}. "
                         "Answer again following the rules exactly, or reply with " + REFUSAL_TAG + "."})
        return msgs

    async def _call_model(self, rec: RequestRecord, messages: list[dict], on_text) -> Completion:
        """One logical model call with governor + bounded, deadline-aware retries."""
        est = Governor.estimate(sum(ntokens(m["content"]) + 4 for m in messages), self.max_output_tokens)
        attempt = 0
        while True:
            remaining = rec.deadline - time.monotonic()
            if remaining <= 0.2:
                raise ProviderTimeout("deadline reached before model call")
            res = None
            if self.governor:
                t_w = time.monotonic()
                try:
                    res, waited = await self.governor.acquire(est, deadline=rec.deadline - 1.0)
                finally:
                    rec.limiter_wait_ms += (time.monotonic() - t_w) * 1000
                if waited > 0.01:
                    rec.ev("limiter_wait_end")
            rec.ev(f"model_call_{rec.model_calls + 1}_start")
            rec.model_calls += 1
            t0 = time.monotonic()
            try:
                comp = await self.provider.stream(
                    messages, self.max_output_tokens, on_text=on_text,
                    first_token_timeout=min(self.first_token_timeout, max(0.5, remaining)),
                    total_timeout=max(0.5, rec.deadline - time.monotonic()))
                rec.model_ms += (time.monotonic() - t0) * 1000
                rec.http_status = comp.http_status
                if comp.ttft_ms is not None:
                    rec.model_ttft_ms.append(round(comp.ttft_ms, 1))
                rec.tokens_in += comp.tokens_in or 0
                rec.tokens_out += comp.tokens_out or 0
                rec.reasoning_tokens += comp.reasoning_tokens or 0
                if self.governor and res:
                    used = (comp.tokens_in or 0) + (comp.tokens_out or 0)
                    self.governor.reconcile(res, used if comp.tokens_in is not None else None)
                    self.governor.observe_headers(comp.headers)
                rec.ev(f"model_call_{rec.model_calls}_end")
                return comp
            except ProviderError as e:
                rec.model_ms += (time.monotonic() - t0) * 1000
                rec.http_status = e.http_status
                partial = getattr(e, "partial", None)
                if partial is not None:
                    rec.tokens_out += partial.tokens_out or 0
                if self.governor and res:
                    if isinstance(e, RateLimited):
                        self.governor.refund(res)
                        if e.daily:
                            self.governor.mark_daily_exhausted(e.limit_kind, e.limit_value, e.limit_used)
                        else:
                            self.governor.on_429(e.retry_after)
                    self.governor.observe_headers(e.headers)
                rec.ev(f"model_call_{rec.model_calls}_error:{type(e).__name__}")
                if rec.sentences_released > 0 or not e.retryable or attempt >= self.retry.max_retries \
                        or self.retry.mode == "none":
                    raise
                wait = self.retry.wait_for(attempt, e)
                if time.monotonic() + wait + 1.0 > rec.deadline:
                    rec.ev("retry_skipped_deadline")
                    raise
                attempt += 1
                rec.retries += 1
                rec.ev(f"retry_{attempt}_wait_start")
                t_r = time.monotonic()
                await asyncio.sleep(wait)
                rec.retry_wait_ms += (time.monotonic() - t_r) * 1000

    def _release(self, rec: RequestRecord, text: str, on_release) -> None:
        if rec.first_token_ms is None:
            rec.first_token_ms = (time.monotonic() - rec.arrival) * 1000
            rec.ev("first_token_to_user")
        rec.released.append(text)
        rec.sentences_released += 1
        if on_release:
            on_release(text)

    async def generate_and_validate(self, rec: RequestRecord, hits: list[Hit], on_release=None) -> None:
        label_to_hit = {h.label: h for h in hits}
        feedback = None
        for round_ in range(2 if self.allow_regenerate else 1):
            gate = SentenceGate()
            state = {"refusal": None, "failed": None, "head": "", "decided": False}

            def handle(sentence: str):
                if state["failed"]:
                    return
                why = validate_sentence(sentence, label_to_hit, rec.question)
                if why:
                    state["failed"] = (sentence, why)
                    rec.sentences_rejected += 1
                    return
                rendered, cites = render(sentence, label_to_hit)
                rec.citations.extend(c for c in cites if c not in rec.citations)
                self._release(rec, rendered, on_release)

            def on_text(piece: str):
                if state["refusal"] is not None or state["failed"]:
                    return
                if not state["decided"]:
                    # hold the first characters until we know whether this is a refusal
                    state["head"] += normalise_model_text(piece)
                    h = state["head"].lstrip()
                    if len(h) < len(REFUSAL_TAG) and REFUSAL_TAG.startswith(h):
                        return
                    state["decided"] = True
                    if h.startswith(REFUSAL_TAG):
                        state["refusal"] = h
                        return
                    piece = state["head"]
                for s in gate.feed(normalise_model_text(piece)):
                    handle(s)

            comp = await self._call_model(rec, self._messages(rec, hits, feedback), on_text)
            rec.answer_raw = comp.text
            if comp.text.lstrip().startswith(REFUSAL_TAG):
                state["refusal"] = comp.text.strip()
            elif not state["failed"]:
                pending = gate.feed(state["head"]) if not state["decided"] else []
                for s in pending + gate.flush():
                    handle(s)
            if state["refusal"] is not None and rec.sentences_released == 0:
                msg = comp.text.strip().split(":", 1)[-1].strip() or "The papers do not cover this."
                msg = CITE_RE.sub("", msg).strip()
                self._release(rec, f"The three papers in this library do not answer this. {msg}", on_release)
                rec.decision = "refuse_model"
                break
            if not state["failed"]:
                rec.decision = "answer" if rec.sentences_released else "empty"
                break
            # a sentence failed validation
            if rec.sentences_released > 0:
                self._release(rec, WITHHELD, on_release)
                rec.decision = "answer_partial"
                break
            feedback = f'the sentence "{state["failed"][0][:200]}" failed: {state["failed"][1]}'
            rec.ev("validation_failed_regenerate")
            if round_ == 1 or not self.allow_regenerate:
                break
        if rec.sentences_released == 0:
            self._release(rec, CANNOT_VERIFY, on_release)
            rec.decision = "refuse_unverifiable"

    # ---- full run
    async def run(self, rec: RequestRecord, on_release=None) -> RequestRecord:
        try:
            if not (3 <= len(rec.question) <= 1000):
                rec.status, rec.error_type, rec.decision = "error", "invalid_input", "reject_input"
                return rec
            hits = self.retrieve_and_plan(rec)
            if not hits or max(h.bm25 for h in hits) < MIN_TOP_BM25:
                self._release(rec, "The three papers in this library do not contain information about this question.",
                              on_release)
                rec.decision = "refuse_no_evidence"
                return rec
            await self.generate_and_validate(rec, hits, on_release)
        except LimitReject as e:
            rec.status = "rate_limited"
            rec.error_type = e.kind
            rec.decision = "reject_limit"
        except RateLimited as e:
            rec.status = "rate_limited"
            rec.error_type = "provider_429_daily" if e.daily else "provider_429"
            rec.decision = "reject_limit"
        except ProviderTimeout as e:
            rec.status = "timeout"
            rec.error_type = "provider_timeout" if "deadline" not in str(e) else "deadline"
        except ProviderError as e:
            rec.status = "error"
            rec.error_type = type(e).__name__
        except asyncio.CancelledError:
            rec.status = "timeout"
            rec.error_type = "user_gave_up"
            raise
        finally:
            rec.total_ms = (time.monotonic() - rec.arrival) * 1000
            rec.answer = " ".join(rec.released)
            rec.ev("done")
        return rec


def short_paper(key: str) -> str:
    return SHORT[key]
