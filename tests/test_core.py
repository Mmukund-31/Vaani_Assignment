import asyncio
import json
import random

import pytest

from relay.limits import Governor, LimitReject
from relay.pipeline import (CANNOT_VERIFY, WITHHELD, Pipeline, RetryPolicy, SentenceGate, validate_sentence)
from relay.providers import MockProvider, RateLimited, ServerError, parse_duration
from relay.retrieval import SETTINGS, build_index, mentioned_papers
from relay.scheduler import Relay, SchedulerConfig

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- ingestion / retrieval
def test_manifest_pins_exact_versions():
    m = json.loads((ROOT / "papers" / "manifest.json").read_text())
    got = {(p["arxiv_id"], p["version"]) for p in m["papers"]}
    assert got == {("1706.03762", "v7"), ("2410.00037", "v1"), ("2507.07955", "v2")}
    for p in m["papers"]:
        assert p["title_matches"] and p["version_listed"] and p["title_in_pdf_page1"]
        assert len(p["sha256"]) == 64


def test_chunks_keep_page_and_section_metadata():
    ix = build_index(SETTINGS["A"])
    assert all(c.page_start >= 1 and c.page_end >= c.page_start for c in ix.chunks)
    assert not any(c.section.lower() == "references" for c in ix.chunks)
    secs = {c.section for c in ix.chunks if c.paper == "attention"}
    assert "3.2.1 Scaled Dot-Product Attention" in secs


def test_retrieval_finds_known_passage():
    ix = build_index(SETTINGS["B"])
    hits = ix.search("Why scale dot products by square root of dk softmax gradients")
    assert any(h.chunk.paper == "attention" and h.chunk.page_start <= 4 <= h.chunk.page_end for h in hits)


def test_paper_routing_aliases():
    assert set(mentioned_papers("Compare Moshi and H-Net")) == {"moshi", "hnet"}


# ---------------------------------------------------------------- validation
class _H:
    def __init__(self, text):
        self.chunk = type("C", (), {"text": text, "citation": lambda s: "[cite]"})()


def test_validate_sentence_rules():
    hits = {"P1": _H("We use a rate of Pdrop = 0.1 for the base model."), "P2": _H("trained for 300,000 steps")}
    q = "What dropout?"
    assert validate_sentence("The base model uses dropout 0.1 [P1].", hits, q) is None
    assert validate_sentence("The base model uses dropout 0.1.", hits, q) == "no citation"
    assert "unknown" in validate_sentence("Dropout is 0.1 [P7].", hits, q)
    assert "number" in validate_sentence("Dropout is 0.3 [P1].", hits, q)
    assert validate_sentence("It ran 300000 steps [P2].", hits, q) is None  # comma-normalised


def test_sentence_gate_handles_decimals_and_trailing_citations():
    g = SentenceGate()
    out = []
    for piece in ["Rate is 12", ".5 Hz [P1]. It uses", " 8 codebooks. [P2] Done", " here [P1]."]:
        out += g.feed(piece)
    out += g.flush()
    assert out == ["Rate is 12.5 Hz [P1].", "It uses 8 codebooks. [P2]", "Done here [P1]."]


# ---------------------------------------------------------------- pipeline with mock provider
def _pipe(provider, **kw):
    return Pipeline(provider, build_index(SETTINGS["A"]), **kw)


async def test_answerable_question_cites_real_page():
    p = _pipe(MockProvider())
    rec = await p.run(Pipeline.intake("Why does the Transformer scale dot products by sqrt dk?", 20))
    assert rec.status == "ok" and rec.decision == "answer"
    assert "arXiv:1706.03762v7" in rec.answer and rec.first_token_ms is not None


async def test_model_refusal_is_released_as_refusal():
    p = _pipe(MockProvider(responder=lambda m, i: "NOT_IN_LIBRARY: Moshi is not evaluated on WMT."))
    rec = await p.run(Pipeline.intake("What BLEU does Moshi get on WMT14?", 20))
    assert rec.decision == "refuse_model" and "do not answer" in rec.answer


async def test_no_lexical_evidence_refuses_without_model_call():
    prov = MockProvider()
    rec = await _pipe(prov).run(Pipeline.intake("qwzx plorf blargh", 20))
    assert rec.decision == "refuse_no_evidence" and prov.calls == 0


async def test_uncited_first_sentence_triggers_one_regeneration():
    answers = ["Dropout is everywhere.", None]

    def resp(msgs, i):
        if i == 0:
            return answers[0]
        from relay.providers import default_mock_answer
        return default_mock_answer(msgs, i)
    prov = MockProvider(responder=resp)
    rec = await _pipe(prov).run(Pipeline.intake("What dropout rate does the base Transformer use?", 20))
    assert prov.calls == 2 and rec.decision == "answer"


async def test_unverifiable_twice_gives_safe_refusal():
    prov = MockProvider(responder=lambda m, i: "It is 99.9 percent [P1].")
    rec = await _pipe(prov).run(Pipeline.intake("What dropout rate does the base Transformer use?", 20))
    assert rec.decision == "refuse_unverifiable" and rec.answer == CANNOT_VERIFY and prov.calls == 2


async def test_bad_later_sentence_is_withheld_not_sent():
    def resp(msgs, i):
        from relay.providers import default_mock_answer
        return default_mock_answer(msgs, i) + " Also it is 999 [P1]."
    rec = await _pipe(MockProvider(responder=resp)).run(Pipeline.intake("What dropout rate does the base Transformer use?", 20))
    assert rec.decision == "answer_partial" and rec.answer.endswith(WITHHELD) and "999" not in rec.answer


async def test_comparison_question_retrieves_per_paper():
    p = _pipe(MockProvider())
    rec = await p.run(Pipeline.intake("Compare positional encoding in the Transformer and Moshi's Helium", 20))
    assert rec.retrieval_rounds == 2
    assert {cid.split("-")[0] for cid in rec.retrieved} == {"attention", "moshi"}


async def test_429_is_retried_with_retry_after():
    prov = MockProvider(errors={0: RateLimited("slow down", retry_after=0.05)})
    rec = await _pipe(prov, retry=RetryPolicy(base_s=0.01)).run(Pipeline.intake("What is the Mimi frame rate?", 20))
    assert rec.status == "ok" and rec.retries == 1 and rec.retry_wait_ms >= 50


async def test_daily_429_is_not_retried():
    prov = MockProvider(errors={0: RateLimited("tokens per day exceeded", daily=True)})
    rec = await _pipe(prov).run(Pipeline.intake("What is the Mimi frame rate?", 20))
    assert rec.status == "rate_limited" and rec.retries == 0 and prov.calls == 1


async def test_retries_are_bounded():
    prov = MockProvider(errors={i: ServerError("500") for i in range(10)})
    rec = await _pipe(prov, retry=RetryPolicy(base_s=0.001, max_retries=2)).run(
        Pipeline.intake("What is the Mimi frame rate?", 20))
    assert rec.status == "error" and prov.calls == 3 and rec.retries == 2


async def test_retry_skipped_when_deadline_too_close():
    prov = MockProvider(errors={0: RateLimited("x", retry_after=30)})
    rec = await _pipe(prov).run(Pipeline.intake("What is the Mimi frame rate?", 5))
    assert rec.status == "rate_limited" and rec.retries == 0


async def test_provider_timeout():
    rec = await _pipe(MockProvider(delay=2.0), per_call_first_token_timeout=0.2, retry=RetryPolicy(mode="none")).run(
        Pipeline.intake("What is the Mimi frame rate?", 20))
    assert rec.status == "timeout"


# ---------------------------------------------------------------- governor
async def test_governor_rpm_bucket():
    t = [0.0]
    g = Governor(rpm=2, tpm=None, rpd=None, tpd=None, clock=lambda: t[0])
    await g.acquire(10, None)
    await g.acquire(10, None)
    with pytest.raises(LimitReject) as e:
        await g.acquire(10, deadline=5.0)
    assert e.value.kind == "rpm" and e.value.wait_s == pytest.approx(30.0)   # refills 2/60 per s
    t[0] = 30.0
    await g.acquire(10, deadline=31.0)


async def test_governor_tpm_and_reconcile():
    t = [0.0]
    g = Governor(rpm=None, tpm=1000, rpd=None, tpd=None, clock=lambda: t[0])
    r, _ = await g.acquire(800, None)
    with pytest.raises(LimitReject):
        await g.acquire(400, deadline=1.0)
    g.reconcile(r, 300)          # actual usage was smaller than the estimate -> 500 returned
    await g.acquire(400, deadline=1.0)


async def test_governor_daily_budget_refills_continuously():
    t = [0.0]
    g = Governor(rpm=None, tpm=None, rpd=None, tpd=1000, day_seconds=100, clock=lambda: t[0])
    await g.acquire(900, None)
    with pytest.raises(LimitReject) as e:
        await g.acquire(200, None)
    assert e.value.kind == "daily_tokens" and e.value.wait_s == pytest.approx(10.0)
    t[0] = 10.0
    await g.acquire(200, None)


def test_governor_estimate_caps_output_reservation():
    assert Governor.estimate(900, 2000) == 1200 and Governor.estimate(81, 5) == 86


def test_governor_adopts_changed_limits_from_headers():
    g = Governor(rpm=30, tpm=8000, rpd=1000, tpd=None)
    g.observe_headers({"x-ratelimit-limit-tokens": "6000", "x-ratelimit-limit-requests": "1000",
                       "x-ratelimit-remaining-requests": "990", "x-ratelimit-remaining-tokens": "100"})
    assert g.tpm == 6000 and g.rpd_b.level <= 990 and g.tpm_b.level <= 100 and g.limit_changes


def test_parse_duration():
    assert parse_duration("2m59.56s") == pytest.approx(179.56)
    assert parse_duration("120ms") == pytest.approx(0.12)
    assert parse_duration("7") == 7


def test_backoff_has_jitter_and_respects_retry_after():
    pol = RetryPolicy(rng=random.Random(1))
    waits = {round(pol.wait_for(2, ServerError("x")), 3) for _ in range(20)}
    assert len(waits) > 10 and max(waits) <= 2.0
    assert pol.wait_for(0, RateLimited("x", retry_after=3.0)) >= 3.0
    assert RetryPolicy(mode="immediate").wait_for(3, RateLimited("x", retry_after=3.0)) == 0


# ---------------------------------------------------------------- scheduler
async def test_queue_is_bounded_and_concurrency_capped():
    prov = MockProvider(delay=0.2)
    relay = Relay(_pipe(prov), SchedulerConfig(max_concurrency=2, max_queue=3, deadline_s=10, predictive_reject=False))
    recs = await asyncio.gather(*[relay.handle("What is the Mimi frame rate?") for _ in range(8)])
    statuses = [r.status for r in recs]
    assert statuses.count("rate_limited") == 3          # 2 running + 3 queued accepted, 3 shed
    assert relay.peak_queue == 3 and relay.active == 0 and relay.waiting == 0


async def test_user_gives_up_and_task_is_cancelled():
    relay = Relay(_pipe(MockProvider(delay=5.0)), SchedulerConfig(max_concurrency=1, max_queue=5, deadline_s=0.3))
    rec = await relay.handle("What is the Mimi frame rate?")
    assert rec.status == "timeout" and rec.error_type in ("user_gave_up", "provider_timeout", "deadline")
    await asyncio.sleep(0.05)
    assert relay.active == 0


async def test_daily_exhausted_rejects_at_admission_without_model_call():
    t = [0.0]
    g = Governor(rpm=None, tpm=None, rpd=1, tpd=None, clock=lambda: t[0])
    prov = MockProvider()
    relay = Relay(_pipe(prov, governor=g), SchedulerConfig(), governor=g)
    first = await relay.handle("What is the Mimi frame rate?")
    second = await relay.handle("What is the Mimi frame rate?")
    assert first.status == "ok" and second.status == "rate_limited" and second.error_type == "daily_budget"
    assert prov.calls == 1 and "refills" in second.answer


# ---------------------------------------------------------------- degradation + token-aware admission
async def test_degrades_to_smaller_context_when_tokens_are_scarce():
    g = Governor(rpm=None, tpm=4000, rpd=None, tpd=None)
    g.tpm_b.level = 1200          # enough for a small-context request soon, not for a full one
    p = Pipeline(MockProvider(), build_index(SETTINGS["D"]), governor=g, fallback_index=build_index(SETTINGS["A"]))
    rec = await p.run(Pipeline.intake("What is the frame rate of the Mimi codec?", 30))
    assert rec.degraded and rec.setting_used == "A" and rec.status == "ok"


async def test_no_degradation_when_budget_is_fine():
    g = Governor(rpm=None, tpm=8000, rpd=None, tpd=None)
    p = Pipeline(MockProvider(), build_index(SETTINGS["D"]), governor=g, fallback_index=build_index(SETTINGS["A"]))
    rec = await p.run(Pipeline.intake("What is the frame rate of the Mimi codec?", 30))
    assert not rec.degraded and rec.setting_used == "D"


async def test_token_aware_admission_rejects_at_arrival_not_after_waiting():
    g = Governor(rpm=None, tpm=2600, rpd=None, tpd=None)
    relay = Relay(_pipe(MockProvider(delay=0.05), governor=g),
                  SchedulerConfig(max_concurrency=1, max_queue=50, deadline_s=10), governor=g)
    recs = await asyncio.gather(*[relay.handle("What is the Mimi frame rate?") for _ in range(12)])
    shed = [r for r in recs if r.error_type == "predicted_wait"]
    assert shed and all(r.total_ms < 50 for r in shed)       # rejected immediately, not after waiting


async def test_fullwidth_and_zero_width_citations_are_accepted():
    # real gpt-oss outputs from the first benchmark run (A3 setting A, M1 setting C)
    def resp(msgs, i):
        from relay.providers import default_mock_answer
        return default_mock_answer(msgs, i).replace("[P1]", "【P1】") + " Again it holds [​P1]."
    rec = await _pipe(MockProvider(responder=resp)).run(Pipeline.intake("What dropout rate does the base Transformer use?", 20))
    assert rec.decision == "answer" and rec.sentences_released == 2


def test_tpd_learned_from_429_body():
    e = RateLimited("429: on tokens per day (TPD): Limit 15000, Used 14800, Requested 2000. Please try again",
                    daily=True)
    assert (e.limit_kind, e.limit_value, e.limit_used) == ("TPD", 15000, 14800)
    g = Governor(rpm=30, tpm=8000, rpd=1000, tpd=200_000)
    g.mark_daily_exhausted(e.limit_kind, e.limit_value, e.limit_used)
    assert g.tpd == 15000 and g.daily_exhausted(1300) == "daily_tokens"
    assert 0 < g.daily_capacity_back_in(1300) / 60 < 24 * 60
