"""Stand-in model service: a controllable imitation of Groq's OpenAI-compatible streaming endpoint.

  uvicorn standin.server:app --port 8001

POST /openai/v1/chat/completions   streaming (SSE) chat, same wire format/headers/429 bodies as Groq
GET  /admin/config | POST /admin/config   read / change behaviour at runtime (partial updates allowed)
POST /admin/reset                   reset buckets, counters and the RNG seed
GET  /admin/stats                   counters (requests, 429s by kind, errors, peak in-flight)

Calibration (from results/groq_calibration.json, gpt-oss-120b, 2026-11-13; 15 paced requests):
  TTFT median ~= 404 ms + 0.102 ms x prompt_tokens  (491 ms @ ~850 tok, 682 ms @ ~2.7K tok)
  TTFT spread: lognormal sigma 0.22 (p90/p50 = 1.32 on medium prompts)
  output speed ~560 visible tok/s, delivered in bursts (inter-chunk p50 ~1 ms)
  limits: token buckets RPM 30 / TPM 8000 / RPD 1000 / TPD 200K, TPM charged prompt + min(max_tokens, 300)
  429: JSON body naming the limit, retry-after = ceil(deficit / refill rate), fast (~60-90 ms)
Not calibrated (assumptions, see report): load-dependent slowdown, error rate, tail spikes, in-flight cap.
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time
import uuid
from typing import Optional

import tiktoken
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from relay.pipeline import SentenceGate

_enc = tiktoken.get_encoding("cl100k_base")


class Config(BaseModel):
    seed: int = 1234
    # latency (calibrated)
    ttft_base_ms: float = 404.0
    ttft_per_prompt_token_ms: float = 0.102
    ttft_sigma: float = 0.22
    tokens_per_s: float = 560.0
    tokens_per_chunk: int = 3
    output_tokens_median: float = 120.0
    output_tokens_sigma: float = 0.35
    # tails and faults (assumed; tunable per scenario)
    slow_prob: float = 0.01            # "slow for no visible reason": TTFT x U(slow_min, slow_max)
    slow_min: float = 4.0
    slow_max: float = 12.0
    stall_prob: float = 0.0            # "succeeds slowly": pauses mid-stream for stall_s
    stall_s: float = 8.0
    error_rate: float = 0.0            # HTTP 500 before streaming
    fail_every_nth: int = 0            # deterministic: every n-th request fails with 500
    midstream_error_rate: float = 0.0  # stream breaks after some tokens
    # load degradation (assumed: Groq showed ~+35% total time with 34 tiny requests in flight)
    load_knee: int = 16
    load_alpha: float = 0.5            # latency x (1 + alpha * max(0, inflight - knee) / knee)
    max_inflight: int = 0              # 0 = unlimited; else 503 over capacity
    # limits (calibrated: Groq free tier for gpt-oss-120b)
    rpm: Optional[int] = 30
    tpm: Optional[int] = 8000
    rpd: Optional[int] = 1000
    tpd: Optional[int] = 200_000
    day_seconds: float = 86400.0
    output_reservation_cap: int = 300
    report_tpm_headers: bool = True


class Bucket:
    def __init__(self, cap, period):
        self.cap, self.period = cap, period
        self.level = cap or 0
        self.t = time.monotonic()

    def refill(self):
        now = time.monotonic()
        if self.cap:
            self.level = min(self.cap, self.level + (now - self.t) * self.cap / self.period)
        self.t = now

    def deficit_wait(self, n):
        if not self.cap:
            return 0.0
        self.refill()
        return 0.0 if self.level >= n else (n - self.level) / (self.cap / self.period)

    def take(self, n):
        if self.cap:
            self.refill()
            self.level -= n

    def give(self, n):
        if self.cap:
            self.refill()
            self.level = min(self.cap, self.level + n)


class State:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.rpm = Bucket(cfg.rpm, 60)
        self.tpm = Bucket(cfg.tpm, 60)
        self.rpd = Bucket(cfg.rpd, cfg.day_seconds)
        self.tpd = Bucket(cfg.tpd, cfg.day_seconds)
        self.inflight = 0
        self.tasks: set = set()
        self.n = 0
        self.stats = {"requests": 0, "ok": 0, "429_rpm": 0, "429_tpm": 0, "429_rpd": 0, "429_tpd": 0,
                      "500": 0, "503": 0, "midstream_errors": 0, "abandoned": 0, "peak_inflight": 0}

    def resize(self, cfg: Config):
        """Apply new limits without resetting usage (that's how a provider changes limits under you)."""
        for name, period in (("rpm", 60), ("tpm", 60), ("rpd", cfg.day_seconds), ("tpd", cfg.day_seconds)):
            b: Bucket = getattr(self, name)
            b.refill()
            new = getattr(cfg, name)
            if new != b.cap:
                used = (b.cap - b.level) if b.cap else 0
                b.cap, b.period = new, period
                b.level = (new - used) if new else 0
        self.cfg = cfg


S = State(Config())
app = FastAPI(title="Relay stand-in model")


def ntok(text: str) -> int:
    return len(_enc.encode(text, disallowed_special=()))


def headers() -> dict:
    c = S.cfg
    h = {}
    if c.rpd:
        S.rpd.refill()
        h["x-ratelimit-limit-requests"] = str(c.rpd)
        h["x-ratelimit-remaining-requests"] = str(max(0, int(S.rpd.level)))
        h["x-ratelimit-reset-requests"] = f"{max(0.0, (c.rpd - S.rpd.level) * c.day_seconds / c.rpd):.2f}s"
    if c.tpm and c.report_tpm_headers:
        S.tpm.refill()
        h["x-ratelimit-limit-tokens"] = str(c.tpm)
        h["x-ratelimit-remaining-tokens"] = str(max(0, int(S.tpm.level)))
        h["x-ratelimit-reset-tokens"] = f"{max(0.0, (c.tpm - S.tpm.level) * 60 / c.tpm):.3f}s"
    return h


def too_many(kind: str, limit: int, used: float, requested: int, wait: float) -> JSONResponse:
    S.stats[f"429_{kind}"] += 1
    names = {"rpm": "requests per minute (RPM)", "tpm": "tokens per minute (TPM)",
             "rpd": "requests per day (RPD)", "tpd": "tokens per day (TPD)"}
    msg = (f"Rate limit reached for model `standin-gpt-oss-120b` in organization `org_standin` service tier "
           f"`on_demand` on {names[kind]}: Limit {limit}, Used {int(used)}, Requested {requested}. "
           f"Please try again in {wait:.3f}s.")
    h = headers()
    h["retry-after"] = str(max(1, math.ceil(wait)))
    return JSONResponse({"error": {"message": msg, "type": "tokens" if "t" in kind[0] else "requests",
                                   "code": "rate_limit_exceeded"}}, status_code=429, headers=h)


def synth_answer(messages: list[dict], n_tokens: int) -> str:
    """Grounded-looking text built from the passages in the prompt, cited with their labels, so the real
    validator accepts it. Refuses if the prompt holds no passages."""
    user = "\n".join(m["content"] for m in messages if m["role"] == "user")
    passages = re.findall(r"\[(P\d+)\] \([^)]*\)\n(.+?)(?=\n\n\[P\d+\]|\n\nQuestion:|$)", user, re.S)
    if not passages:
        return "NOT_IN_LIBRARY: The provided passages do not address this question."
    out, used = [], 0
    for label, text in passages:
        # split exactly like the pipeline does, so each emitted sentence carries its own citation
        gate = SentenceGate()
        sentences = gate.feed(re.sub(r"\s+", " ", text.strip())) + gate.flush()
        for sent in sentences:
            if len(sent) < 25 or sent.endswith(("e.g.", "i.e.")):
                continue
            s = f"{sent.rstrip('.')} [{label}]."
            out.append(s)
            used += ntok(s)
            if used >= n_tokens or len(out) >= 5:
                return " ".join(out)
    return " ".join(out) or "NOT_IN_LIBRARY: The provided passages do not address this question."


@app.post("/openai/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    c = S.cfg
    S.n += 1
    S.stats["requests"] += 1
    messages = body.get("messages", [])
    prompt_tokens = sum(ntok(m.get("content", "")) + 4 for m in messages)
    max_tokens = int(body.get("max_completion_tokens") or body.get("max_tokens") or 1024)
    charge = prompt_tokens + min(max_tokens, c.output_reservation_cap)
    await asyncio.sleep(S.rng.uniform(0.04, 0.09))  # measured: 429s come back in ~60-90 ms

    # ---- limits, checked in the order Groq reports them
    if (w := S.rpd.deficit_wait(1)) > 0:
        return too_many("rpd", c.rpd, c.rpd - S.rpd.level, 1, w)
    if (w := S.tpd.deficit_wait(charge)) > 0:
        return too_many("tpd", c.tpd, c.tpd - S.tpd.level, charge, w)
    if (w := S.rpm.deficit_wait(1)) > 0:
        return too_many("rpm", c.rpm, c.rpm - S.rpm.level, 1, w)
    if (w := S.tpm.deficit_wait(charge)) > 0:
        return too_many("tpm", c.tpm, c.tpm - S.tpm.level, charge, w)
    if c.max_inflight and S.inflight >= c.max_inflight:
        S.stats["503"] += 1
        return JSONResponse({"error": {"message": "over capacity", "type": "service_unavailable"}}, status_code=503)
    for b, n in ((S.rpm, 1), (S.rpd, 1), (S.tpm, charge), (S.tpd, charge)):
        b.take(n)
    h = headers()

    # ---- faults
    if (c.fail_every_nth and S.n % c.fail_every_nth == 0) or S.rng.random() < c.error_rate:
        S.stats["500"] += 1
        for b in (S.tpm, S.tpd):
            b.give(charge)
        return JSONResponse({"error": {"message": "internal server error (injected)", "type": "internal_error"}},
                            status_code=500, headers=h)

    # ---- draw this request's behaviour up front (keeps the RNG sequence independent of timing)
    load = 1 + c.load_alpha * max(0, S.inflight + 1 - c.load_knee) / max(1, c.load_knee)
    ttft = (c.ttft_base_ms + c.ttft_per_prompt_token_ms * prompt_tokens) * math.exp(S.rng.gauss(0, c.ttft_sigma))
    if S.rng.random() < c.slow_prob:
        ttft *= S.rng.uniform(c.slow_min, c.slow_max)
    ttft *= load
    n_out = int(min(max_tokens, max(8, S.rng.lognormvariate(math.log(c.output_tokens_median), c.output_tokens_sigma))))
    stall_at = S.rng.uniform(0.2, 0.8) if S.rng.random() < c.stall_prob else None
    break_at = S.rng.uniform(0.2, 0.8) if S.rng.random() < c.midstream_error_rate else None
    text = synth_answer(messages, n_out)
    pieces = re.findall(r"\S+\s*", text)
    rid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    # The provider keeps working on (and has charged for) a request even if the client gives up, so the request
    # occupies capacity for its full expected duration regardless of the connection. This is what lets client
    # retries-on-timeout amplify load.
    expected_s = (ttft / 1000 + math.ceil(len(pieces) / c.tokens_per_chunk) * c.tokens_per_chunk / c.tokens_per_s * load
                  + (c.stall_s * load if stall_at is not None else 0))
    S.inflight += 1
    S.stats["peak_inflight"] = max(S.stats["peak_inflight"], S.inflight)

    st = S   # bind this request to the state it was admitted under (an /admin/reset swaps S)

    async def release():
        await asyncio.sleep(expected_s)
        st.inflight -= 1
    st.tasks.add(t := asyncio.create_task(release()))
    t.add_done_callback(st.tasks.discard)

    async def gen():
        try:
            await asyncio.sleep(ttft / 1000)
            per_chunk = c.tokens_per_chunk / c.tokens_per_s * load
            sent_tokens = 0
            stalled = False
            for i in range(0, len(pieces), c.tokens_per_chunk):
                frac = i / max(1, len(pieces))
                if stall_at is not None and not stalled and frac >= stall_at:
                    await asyncio.sleep(c.stall_s * load)   # one stall per request
                    stalled = True
                if break_at is not None and frac >= break_at:
                    S.stats["midstream_errors"] += 1
                    yield f"data: {json.dumps({'error': {'message': 'stream interrupted (injected)'}})}\n\n"
                    return
                chunk = "".join(pieces[i:i + c.tokens_per_chunk])
                sent_tokens += ntok(chunk)
                yield "data: " + json.dumps({"id": rid, "object": "chat.completion.chunk", "model": body.get("model"),
                                             "choices": [{"index": 0, "delta": {"content": chunk}}]}) + "\n\n"
                await asyncio.sleep(per_chunk)
            usage = {"prompt_tokens": prompt_tokens, "completion_tokens": sent_tokens,
                     "total_tokens": prompt_tokens + sent_tokens}
            # reconcile the admission charge to actual usage (as the governor assumes the provider does)
            for b in (S.tpm, S.tpd):
                b.give(charge - (prompt_tokens + sent_tokens))
            yield "data: " + json.dumps({"id": rid, "object": "chat.completion.chunk", "choices": [],
                                         "usage": usage}) + "\n\n"
            yield "data: [DONE]\n\n"
            S.stats["ok"] += 1
        except asyncio.CancelledError:
            S.stats["abandoned"] += 1   # client went away; work continues to count via release()
            raise

    return StreamingResponse(gen(), media_type="text/event-stream", headers=h)


@app.get("/admin/config")
async def get_config():
    return S.cfg.model_dump()


@app.post("/admin/config")
async def set_config(update: dict):
    cfg = S.cfg.model_copy(update=update)
    S.resize(Config(**cfg.model_dump()))
    return S.cfg.model_dump()


@app.post("/admin/reset")
async def reset(update: dict | None = None):
    global S
    S = State(Config(**{**Config().model_dump(), **(update or {})}))
    return S.cfg.model_dump()


@app.get("/admin/stats")
async def stats():
    return {**S.stats, "inflight": S.inflight, "rpm_level": S.rpm.level, "tpm_level": S.tpm.level,
            "rpd_level": S.rpd.level, "tpd_level": S.tpd.level}
