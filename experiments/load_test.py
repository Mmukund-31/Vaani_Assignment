"""Load shapes against the pipeline (scheduler + governor + pipeline -> stand-in over HTTP, or real Groq).

  python -m experiments.load_test --scenario steady            # stand-in (auto-starts it on :8001)
  python -m experiments.load_test --scenario steady --mode groq --rate 3 --minutes 4   # small real run
  python -m experiments.load_test --scenario all

Each run writes results/runs/<run_id>.csv, results/traces/<run_id>.jsonl and <run_id>.config.json
(scenario, arrival schedule, seed, scheduler/governor/stand-in configuration, stand-in counters).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import httpx

from relay.factory import GROQ_LIMITS, make_pipeline
from relay.limits import Governor
from relay.pipeline import RetryPolicy
from relay.records import RunWriter, row, trace
from relay.retrieval import SETTINGS
from relay.scheduler import Relay, SchedulerConfig

ROOT = Path(__file__).resolve().parent.parent
STANDIN = "http://127.0.0.1:8001"

# Offered load is chosen relative to measured capacity: with TPM 8000 and ~1.3-2.3K tokens charged per answer
# the real account sustains roughly 3.5-6 answers/min. "steady" sits below that, "overload" ~4x above it.
SCENARIOS = {
    "steady":        {"shape": "constant", "rate_per_min": 3, "minutes": 5},
    "burst":         {"shape": "burst", "n": 30, "window_s": 5},
    "overload":      {"shape": "constant", "rate_per_min": 20, "minutes": 3},
    "rpm_halved":    {"shape": "constant", "rate_per_min": 6, "minutes": 4,
                      "standin": {"rpm": 15, "tpm": 4000}},     # provider halves per-minute limits silently
    "daily_exhausted": {"shape": "constant", "rate_per_min": 4, "minutes": 5,
                        "standin": {"tpd": 15000}},             # small simulated daily budget runs out mid-run
}
# Retry experiment: rate limits out of the way so only latency matters; the stand-in slows down as work in flight
# grows and keeps working on abandoned requests. Client gives each model call 2.5 s to produce a first token.
_RETRY_BASE = {"shape": "constant", "rate_per_min": 90, "minutes": 2, "concurrency": 24, "ft_timeout": 2.5,
               "no_governor": True,
               "standin": {"rpm": 100000, "tpm": 100000000, "rpd": None, "tpd": None,
                           "load_knee": 6, "load_alpha": 0.6, "slow_prob": 0.08}}
RETRY_SCENARIOS = {
    "retry_none":      {**_RETRY_BASE, "retry_mode": "none", "max_retries": 0},
    "retry_immediate": {**_RETRY_BASE, "retry_mode": "immediate", "max_retries": 3},
    "retry_backoff":   {**_RETRY_BASE, "retry_mode": "backoff", "max_retries": 2},
}


def arrivals(spec: dict, rng: random.Random) -> list[float]:
    if spec["shape"] == "constant":
        n = int(spec["rate_per_min"] * spec["minutes"])
        gap = 60.0 / spec["rate_per_min"]
        return [i * gap for i in range(n)]
    if spec["shape"] == "burst":
        return sorted(rng.uniform(0, spec["window_s"]) for _ in range(spec["n"]))
    raise ValueError(spec["shape"])


def ensure_standin() -> subprocess.Popen | None:
    try:
        httpx.get(f"{STANDIN}/admin/config", timeout=1)
        return None
    except httpx.TransportError:
        pass
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "standin.server:app", "--port", "8001",
                             "--log-level", "warning"], cwd=ROOT)
    for _ in range(100):
        try:
            httpx.get(f"{STANDIN}/admin/config", timeout=1)
            return proc
        except httpx.TransportError:
            time.sleep(0.1)
    raise RuntimeError("stand-in did not start")


async def run(scenario: str, mode: str, setting: str, seed: int, sched: SchedulerConfig, retry: RetryPolicy,
              spec: dict, standin_overrides: dict, label_suffix: str = "", fallback: str | None = "A") -> str:
    rng = random.Random(seed)
    qs = json.loads((ROOT / "evaluation" / "questions.json").read_text(encoding="utf-8"))
    sched_times = arrivals(spec, rng)
    order = [qs[rng.randrange(len(qs))] for _ in sched_times]
    standin_cfg = None
    if mode == "standin":
        standin_cfg = httpx.post(f"{STANDIN}/admin/reset", json={"seed": seed, **standin_overrides}).json()
    # The governor is configured with what we *measured* on Groq; it is not told about stand-in overrides.
    gov = None if spec.get("no_governor") else Governor(**GROQ_LIMITS)
    pipe, _ = make_pipeline(mode, setting, retry=retry, governor=gov, limits={} if gov is None else None,
                            standin_url=f"{STANDIN}/openai/v1", fallback=fallback if gov else None)
    pipe.first_token_timeout = spec.get("ft_timeout", pipe.first_token_timeout)
    relay = Relay(pipe, sched, governor=gov)
    run_id = f"{'real' if mode == 'groq' else 'standin'}-{scenario}{label_suffix}-{time.strftime('%Y%m%dT%H%M%S')}"
    w = RunWriter(run_id)
    label = SETTINGS[setting].label()
    source = pipe.provider.source
    t0 = time.monotonic()
    done = {"n": 0}

    async def one(i: int, at: float, q: dict):
        await asyncio.sleep(max(0.0, t0 + at - time.monotonic()))
        rec = await relay.handle(q["question"], request_id=f"{i:04d}")
        w.write(row(rec, run_id=run_id, scenario=scenario, source=source, setting_label=label,
                    model=pipe.provider.model, question_id=q["id"], deadline_s=sched.deadline_s,
                    offered_at_s=at, seed=seed),
                trace(rec, run_id, {"question_id": q["id"], "offered_at_s": at}))
        done["n"] += 1
        print(f"[{scenario}] {i:3d} t={at:6.1f}s {rec.status:12s} {rec.error_type or rec.decision:18s} "
              f"ttft={rec.first_token_ms and round(rec.first_token_ms)} total={round(rec.total_ms)} "
              f"q={rec.queue_depth} retries={rec.retries}", flush=True)

    await asyncio.gather(*[one(i, at, q) for i, (at, q) in enumerate(zip(sched_times, order))])
    duration = time.monotonic() - t0
    stats = httpx.get(f"{STANDIN}/admin/stats").json() if mode == "standin" else None
    snap = {"run_id": run_id, "scenario": scenario, "mode": mode, "source": source, "seed": seed, "spec": spec,
            "offered": len(sched_times), "arrivals_s": sched_times, "duration_s": duration,
            "scheduler": asdict(sched), "retry": {k: v for k, v in asdict(retry).items() if k != "rng"},
            "governor_limits_configured": GROQ_LIMITS if gov else None,
            "governor_limit_changes": gov.limit_changes if gov else None,
            "governor_provider_429s": gov.provider_429s if gov else None, "peak_queue": relay.peak_queue,
            "retrieval_setting": label, "fallback_setting": fallback, "model": pipe.provider.model, "standin_config": standin_cfg,
            "standin_stats": stats}
    (ROOT / "results" / "runs" / f"{run_id}.config.json").write_text(json.dumps(snap, indent=2))
    await pipe.provider.aclose()
    print(f"== {run_id}: offered={len(sched_times)} duration={duration:.0f}s peak_queue={relay.peak_queue} "
          f"standin={stats}")
    return run_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="steady", choices=list(SCENARIOS) + list(RETRY_SCENARIOS) + ["all", "retry_all"])
    ap.add_argument("--mode", default="standin", choices=["standin", "groq"])
    ap.add_argument("--setting", default="C")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--rate", type=float, help="override rate_per_min")
    ap.add_argument("--minutes", type=float, help="override minutes")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-queue", type=int, default=20)
    ap.add_argument("--deadline", type=float, default=15.0)
    ap.add_argument("--retry", default="backoff", choices=["backoff", "immediate", "none"])
    ap.add_argument("--max-retries", type=int, default=2)
    ap.add_argument("--no-predictive", action="store_true")
    ap.add_argument("--standin", default="{}", help="extra JSON overrides for the stand-in")
    ap.add_argument("--suffix", default="")
    ap.add_argument("--fallback", default="A", help="smaller retrieval setting used under token pressure ('none' to disable)")
    a = ap.parse_args()
    proc = ensure_standin() if a.mode == "standin" else None
    try:
        allspecs = {**SCENARIOS, **RETRY_SCENARIOS}
        names = list(SCENARIOS) if a.scenario == "all" else list(RETRY_SCENARIOS) if a.scenario == "retry_all"             else [a.scenario]
        for name in names:
            spec = dict(allspecs[name])
            if a.rate:
                spec["rate_per_min"] = a.rate
            if a.minutes:
                spec["minutes"] = a.minutes
            overrides = {**spec.pop("standin", {}), **json.loads(a.standin)}
            sched = SchedulerConfig(spec.get("concurrency", a.concurrency), a.max_queue, a.deadline, not a.no_predictive)
            retry = RetryPolicy(mode=spec.get("retry_mode", a.retry), max_retries=spec.get("max_retries", a.max_retries),
                                rng=random.Random(a.seed))
            fb = None if a.fallback == "none" else a.fallback
            asyncio.run(run(name, a.mode, a.setting, a.seed, sched, retry, spec, overrides, a.suffix, fb))
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
