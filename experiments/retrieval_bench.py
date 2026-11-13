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
import time
from pathlib import Path

from relay.factory import make_pipeline
from relay.limits import Governor
from relay.pipeline import Pipeline
from relay.records import RunWriter, row, trace
from relay.retrieval import SETTINGS

ROOT = Path(__file__).resolve().parent.parent


async def run_setting(mode: str, key: str, questions: list[dict], run_id: str) -> None:
    pipe, gov = make_pipeline(mode, key)
    w = RunWriter(run_id)
    label = SETTINGS[key].label()
    for q in questions:
        if gov:
            # pace on the prompt the planner will really build (incl. per-paper re-retrieval), so the measured
            # latency excludes our own throttling (first run paced on the single-search prompt: X3 waited 6.7 s)
            hits = pipe.retrieve_and_plan(Pipeline.intake(q["question"], 60.0))
            est = Governor.estimate(pipe.prompt_tokens(hits, q["question"]), pipe.max_output_tokens)
            await gov.wait_until_fits(est)
        rec = Pipeline.intake(q["question"], 60.0, request_id=q["id"])
        await pipe.run(rec)
        w.write(row(rec, run_id=run_id, scenario=f"retrieval_{key}", source=pipe.provider.source,
                    setting_label=label, model=pipe.provider.model, question_id=q["id"], deadline_s=60.0),
                trace(rec, run_id, {"question_id": q["id"], "setting": label}))
        print(f"{key} {q['id']:3s} {rec.status:12s} {rec.decision:20s} ttft={rec.first_token_ms and round(rec.first_token_ms)} "
              f"tok={rec.tokens_in}+{rec.tokens_out} calls={rec.model_calls} | {rec.answer[:110]!r}", flush=True)
    await pipe.provider.aclose()


async def main_async(a):
    qs = json.loads((ROOT / "evaluation" / "questions.json").read_text(encoding="utf-8"))
    stamp = time.strftime("%Y%m%dT%H%M%S")
    for key in a.settings:
        await run_setting(a.mode, key, qs, f"{a.mode}-retrieval{key}-{stamp}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="groq", choices=["groq", "mock", "standin"])
    ap.add_argument("--settings", nargs="+", default=["A", "C"])
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
