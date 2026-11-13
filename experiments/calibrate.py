"""Real-Groq calibration. Spends a planned, small slice of the daily budget.

  python -m experiments.calibrate models                 # list models (no chat quota)
  python -m experiments.calibrate latency --n 16         # paced streaming requests, two prompt sizes
  python -m experiments.calibrate rpm-probe --n 34       # tiny requests fired together -> find RPM 429 + recovery
  python -m experiments.calibrate tpm-probe              # 4 large prompts back-to-back -> find TPM 429
  python -m experiments.calibrate summarize              # -> results/groq_calibration.json

Every request is appended to results/raw/groq_calibration_raw.jsonl (headers, timings, status, body of errors).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from relay.config import require_groq, settings
from relay.providers import OpenAICompatProvider, ProviderError
from relay.retrieval import SETTINGS, build_index, format_passages

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "results" / "raw" / "groq_calibration_raw.jsonl"


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log(rec: dict) -> None:
    RAW.parent.mkdir(parents=True, exist_ok=True)
    line = re.sub(r"org_[0-9a-z]{10,}", "org_REDACTED", json.dumps(rec))  # account identifier
    with RAW.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def prompt_of_size(kind: str, i: int) -> list[dict]:
    qs = json.loads((ROOT / "evaluation" / "questions.json").read_text(encoding="utf-8"))
    q = qs[i % len(qs)]["question"]
    if kind == "tiny":
        return [{"role": "user", "content": f"Reply with the single word OK. ({i})"}]
    setting = SETTINGS["A"] if kind == "medium" else SETTINGS["D"]
    hits = build_index(setting).search(q)
    return [{"role": "system", "content": "Answer only from the passages, cite [P#]. Be brief."},
            {"role": "user", "content": f"Passages:\n\n{format_passages(hits)}\n\nQuestion: {q}"}]


async def one(prov: OpenAICompatProvider, phase: str, kind: str, i: int, max_tokens: int) -> dict:
    msgs = prompt_of_size(kind, i)
    rec = {"phase": phase, "kind": kind, "i": i, "start_utc": utc(), "model": prov.model}
    t0 = time.perf_counter()
    try:
        c = await prov.stream(msgs, max_tokens, first_token_timeout=30, total_timeout=60)
        rec.update(status="ok", http_status=c.http_status, ttft_ms=c.ttft_ms, total_ms=c.total_ms,
                   tokens_in=c.tokens_in, tokens_out=c.tokens_out, reasoning_tokens=c.reasoning_tokens,
                   n_chunks=len(c.chunk_times_ms), chunk_times_ms=c.chunk_times_ms[:400], headers=c.headers,
                   text=c.text[:300])
    except ProviderError as e:
        rec.update(status=e.status, http_status=e.http_status, total_ms=(time.perf_counter() - t0) * 1000,
                   error=str(e)[:600], error_type=type(e).__name__, headers=e.headers, retry_after=e.retry_after)
    log(rec)
    print(f"{phase:9s} {kind:6s} #{i:<3d} {rec['status']:12s} http={rec.get('http_status')} "
          f"ttft={rec.get('ttft_ms') or 0:7.0f} total={rec['total_ms']:7.0f} in={rec.get('tokens_in')} "
          f"out={rec.get('tokens_out')} rem_tok={rec['headers'].get('x-ratelimit-remaining-tokens')} "
          f"rem_req={rec['headers'].get('x-ratelimit-remaining-requests')} ra={rec['headers'].get('retry-after')}")
    return rec


def provider() -> OpenAICompatProvider:
    s = settings()
    require_groq(s)
    return OpenAICompatProvider(s.groq_base_url, s.groq_api_key, s.groq_model, "real", "groq",
                                reasoning_effort=s.reasoning_effort)


async def cmd_models():
    s = settings()
    if not s.groq_api_key:
        raise SystemExit("GROQ_API_KEY missing in .env")
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {s.groq_api_key}"}) as c:
        r = await c.get(f"{s.groq_base_url}/models", timeout=30)
    r.raise_for_status()
    data = r.json()["data"]
    out = ROOT / "results" / "raw" / "groq_models.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"fetched_utc": utc(), "models": data}, indent=1))
    for m in sorted(data, key=lambda m: m["id"]):
        print(f"{m['id']:45s} ctx={m.get('context_window')} max_out={m.get('max_completion_tokens')} active={m.get('active')}")


async def cmd_latency(n: int, gap: float):
    prov = provider()
    for i in range(n):
        await one(prov, "latency", "medium" if i % 2 == 0 else "large", i, 300)
        await asyncio.sleep(gap)  # pace well under TPM so we measure latency, not throttling
    await prov.aclose()


async def cmd_rpm_probe(n: int, recover_poll: float):
    """Fire n tiny requests at once. Expect ~RPM successes then 429s. Then poll until a request succeeds."""
    prov = provider()
    recs = await asyncio.gather(*[one(prov, "rpm_probe", "tiny", i, 5) for i in range(n)])
    first_429 = [r for r in recs if r["status"] == "rate_limited"]
    if first_429:
        t_hit = time.monotonic()
        for k in range(60):
            await asyncio.sleep(recover_poll)
            r = await one(prov, "recovery", "tiny", 1000 + k, 5)
            if r["status"] == "ok":
                log({"phase": "recovery_result", "seconds_to_recover": time.monotonic() - t_hit,
                     "polls": k + 1, "start_utc": utc()})
                print(f"recovered after {time.monotonic() - t_hit:.1f}s ({k + 1} polls)")
                break
    await prov.aclose()


async def cmd_tpm_probe(n: int):
    prov = provider()
    recs = []
    for i in range(n):
        recs.append(await one(prov, "tpm_probe", "large", 100 + i, 200))
    await prov.aclose()


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100
    f = int(k)
    return xs[f] + (xs[min(f + 1, len(xs) - 1)] - xs[f]) * (k - f)


def cmd_summarize():
    rows = [json.loads(line) for line in RAW.read_text(encoding="utf-8").splitlines() if line.strip()]
    ok = [r for r in rows if r.get("status") == "ok" and r.get("phase") == "latency"]
    out = {"generated_utc": utc(), "n_requests_total": sum(1 for r in rows if "status" in r),
           "model": next((r["model"] for r in rows if r.get("model")), None), "latency": {}}
    for kind in ("medium", "large"):
        rs = [r for r in ok if r["kind"] == kind]
        if not rs:
            continue
        ttft = [r["ttft_ms"] for r in rs if r.get("ttft_ms")]
        tot = [r["total_ms"] for r in rs]
        rates = []
        gaps = []
        for r in rs:
            ct = r.get("chunk_times_ms") or []
            if len(ct) > 2 and r.get("tokens_out"):
                gen_ms = ct[-1] - ct[0]
                vis_tokens = r["tokens_out"] - (r.get("reasoning_tokens") or 0)
                if gen_ms > 0 and vis_tokens > 0:
                    rates.append(vis_tokens / (gen_ms / 1000))
                gaps += [b - a for a, b in zip(ct, ct[1:])]
        out["latency"][kind] = {
            "n": len(rs), "tokens_in_mean": statistics.mean(r["tokens_in"] or 0 for r in rs),
            "tokens_out_mean": statistics.mean(r["tokens_out"] or 0 for r in rs),
            "reasoning_tokens_mean": statistics.mean(r.get("reasoning_tokens") or 0 for r in rs),
            "ttft_ms": {"p50": pct(ttft, 50), "p90": pct(ttft, 90), "max": max(ttft), "min": min(ttft)},
            "total_ms": {"p50": pct(tot, 50), "p90": pct(tot, 90), "max": max(tot), "min": min(tot)},
            "visible_tokens_per_s": {"p50": pct(rates, 50), "min": min(rates) if rates else None,
                                     "max": max(rates) if rates else None},
            "inter_chunk_ms": {"p50": pct(gaps, 50), "p99": pct(gaps, 99)},
        }
    errs = [r for r in rows if r.get("status") not in (None, "ok")]
    out["errors"] = [{k: r.get(k) for k in ("phase", "start_utc", "http_status", "error", "retry_after", "headers")}
                     for r in errs][:20]
    out["header_samples"] = [r["headers"] for r in rows if r.get("headers")][-5:]
    out["recovery"] = [r for r in rows if r.get("phase") == "recovery_result"]
    (ROOT / "results" / "groq_calibration.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out["latency"], indent=1))
    print("errors:", len(errs), " recovery:", out["recovery"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["models", "latency", "rpm-probe", "tpm-probe", "summarize"])
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--gap", type=float, default=4.0)
    ap.add_argument("--poll", type=float, default=2.0)
    a = ap.parse_args()
    if a.cmd == "models":
        asyncio.run(cmd_models())
    elif a.cmd == "latency":
        asyncio.run(cmd_latency(a.n, a.gap))
    elif a.cmd == "rpm-probe":
        asyncio.run(cmd_rpm_probe(a.n, a.poll))
    elif a.cmd == "tpm-probe":
        asyncio.run(cmd_tpm_probe(a.n))
    else:
        cmd_summarize()


if __name__ == "__main__":
    main()
