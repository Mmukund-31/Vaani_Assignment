"""Ask one question.

  python -m relay.cli --mode mock "What is the Mimi frame rate?"
  python -m relay.cli --mode groq --setting C "Why scale dot products by sqrt(d_k)?"
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from .factory import make_pipeline
from .pipeline import Pipeline


async def main_async(a):
    pipe, _ = make_pipeline(a.mode, a.setting)
    rec = Pipeline.intake(a.question, a.deadline)

    def show(s):
        print(s, flush=True)
    await pipe.run(rec, on_release=show)
    await pipe.provider.aclose()
    print(f"\n-- status={rec.status} decision={rec.decision} ttft={rec.first_token_ms and round(rec.first_token_ms)}ms "
          f"total={round(rec.total_ms)}ms calls={rec.model_calls} tokens={rec.tokens_in}+{rec.tokens_out} "
          f"rounds={rec.retrieval_rounds} retries={rec.retries}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--mode", default="mock", choices=["mock", "groq", "standin"])
    ap.add_argument("--setting", default="C")
    ap.add_argument("--deadline", type=float, default=20.0)
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
