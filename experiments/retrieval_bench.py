"""Answer-quality benchmark: every evaluation question under each retrieval setting.

  python -m experiments.retrieval_bench --mode groq --settings A C

Same model, prompt, validator and grading rules for every setting; only retrieval changes.
Requests are sequential and paced so that each starts when the governor would admit it immediately:
Table 3 latency then reflects the pipeline, not our own throttling (limiter_wait_ms is still recorded).
Answers go to results/traces/<run>.jsonl for grading (evaluation/grades.json).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

from relay.factory import make_pipeline
from relay.limits import Governor, LimitReject
from relay.pipeline import Pipeline
from relay.records import RunWriter, row, trace
from relay.retrieval import SETTINGS

ROOT = Path(__file__).resolve().parent.parent


DAILY = ("provider_429_daily", "daily_tokens", "daily_requests")


async def run_setting(mode: str, key: str, questions: list[dict], run_id: str, wait_daily: bool = False) -> None:
    """With wait_daily, a question refused for the DAILY budget is logged (scenario retrieval_<key>_budget_wait,
    so it never counts in Table 3), we sleep until the rolling daily bucket has refilled enough (computed from the
    429 body the governor parsed), and the same question is asked again. Groq does not charge for 429s."""
    pipe, gov = make_pipeline(mode, key)
    w = RunWriter(run_id)
    label = SETTINGS[key].label()
    for q in questions:
        attempt = 0
        while True:
            est = 0
            if gov:
                # pace on the prompt the planner will really build (incl. per-paper re-retrieval), so the measured
                # latency excludes our own throttling (first run paced on the single-search prompt: X3 waited 6.7 s)
                hits = pipe.retrieve_and_plan(Pipeline.intake(q["question"], 60.0))
                est = Governor.estimate(pipe.prompt_tokens(hits, q["question"]), pipe.max_output_tokens)
                try:
                    await gov.wait_until_fits(est)
                except LimitReject as e:
                    if not wait_daily:
                        print(f"stopping {key}: {e}", flush=True)
                        await pipe.provider.aclose()
                        return
                    nap = gov.daily_capacity_back_in(est) + 5
                    print(f"{key} {q['id']}: daily budget short, sleeping {nap:.0f}s", flush=True)
                    await asyncio.sleep(nap)
                    continue
            rid = q["id"] + (f"-retry{attempt}" if attempt else "")
            rec = Pipeline.intake(q["question"], 60.0, request_id=rid)
            await pipe.run(rec)
            daily_hit = rec.status == "rate_limited" and rec.error_type in DAILY
            scen = f"retrieval_{key}_budget_wait" if (daily_hit and wait_daily) else f"retrieval_{key}"
            w.write(row(rec, run_id=run_id, scenario=scen, source=pipe.provider.source,
                        setting_label=label, model=pipe.provider.model, question_id=q["id"], deadline_s=60.0),
                    trace(rec, run_id, {"question_id": q["id"], "setting": label}))
            print(f"{time.strftime('%H:%M:%S')} {key} {rid:10s} {rec.status:12s} {rec.decision:20s} "
                  f"ttft={rec.first_token_ms and round(rec.first_token_ms)} tok={rec.tokens_in}+{rec.tokens_out} "
                  f"calls={rec.model_calls} | "
                  f"{re.sub(r'org_[0-9a-z]{10,}', 'org_REDACTED', rec.error_detail[:160]) if daily_hit else repr(rec.answer[:100])}",
                  flush=True)
            if daily_hit and wait_daily and attempt < 60:
                attempt += 1
                nap = (gov.daily_capacity_back_in(est) if gov else 600) + 5
                print(f"{key} {q['id']}: daily 429, sleeping {nap:.0f}s then asking again", flush=True)
                await asyncio.sleep(nap)
                continue
            break
    await pipe.provider.aclose()


async def main_async(a):
    qs = json.loads((ROOT / "evaluation" / "questions.json").read_text(encoding="utf-8"))
    stamp = time.strftime("%Y%m%dT%H%M%S")
    for key in a.settings:
        await run_setting(a.mode, key, qs, f"{a.mode}-retrieval{key}-{stamp}", a.wait_daily)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="groq", choices=["groq", "mock", "standin"])
    ap.add_argument("--settings", nargs="+", default=["A", "C"])
    ap.add_argument("--wait-daily", action="store_true",
                    help="sleep through daily-budget exhaustion instead of stopping (rolling 24 h bucket)")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
