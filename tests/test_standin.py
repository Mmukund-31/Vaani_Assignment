import threading
import time

import httpx
import pytest
import uvicorn

from relay.pipeline import Pipeline
from relay.providers import OpenAICompatProvider, RateLimited, ServerError, ProviderTimeout
from relay.retrieval import SETTINGS, build_index
from standin import server

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module", autouse=True)
def standin():
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=PORT, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            httpx.get(f"{BASE}/admin/config", timeout=0.5)
            break
        except httpx.TransportError:
            time.sleep(0.05)
    yield
    srv.should_exit = True
    th.join(timeout=5)


def reset(**kw):
    base = {"ttft_base_ms": 20, "ttft_per_prompt_token_ms": 0, "ttft_sigma": 0.0, "slow_prob": 0.0,
            "tokens_per_s": 5000}
    httpx.post(f"{BASE}/admin/reset", json={**base, **kw})


def prov():
    return OpenAICompatProvider(f"{BASE}/openai/v1", "x", "standin", "stand-in", "standin")


MSG = [{"role": "user", "content": "Passages:\n\n[P1] (Moshi, p. 11, Section 3.3.1)\nWe use Q = 8 quantizers "
                                   "each with a codebook size of 2048. At 12.5Hz this is a bitrate of 1.1kbps.\n\n"
                                   "Question: What is Mimi's frame rate?"}]


async def test_streams_in_multiple_chunks_with_usage():
    reset()
    p = prov()
    c = await p.stream(MSG, 200)
    await p.aclose()
    assert len(c.chunk_times_ms) > 3 and "[P1]" in c.text and c.tokens_in > 0 and c.tokens_out > 0
    assert "x-ratelimit-remaining-tokens" in c.headers


async def test_rpm_limit_returns_groq_style_429():
    reset(rpm=2)
    p = prov()
    await p.stream(MSG, 50)
    await p.stream(MSG, 50)
    with pytest.raises(RateLimited) as e:
        await p.stream(MSG, 50)
    await p.aclose()
    assert e.value.retry_after and e.value.retry_after >= 1 and not e.value.daily
    assert "requests per minute" in str(e.value)


async def test_daily_token_budget_exhaustion_is_flagged_daily():
    reset(tpd=450)  # first call charges ~380 then reconciles to ~110 actual; the second charge no longer fits
    p = prov()
    await p.stream(MSG, 300)
    with pytest.raises(RateLimited) as e:
        await p.stream(MSG, 300)
    await p.aclose()
    assert e.value.daily and not e.value.retryable


async def test_deterministic_failures_every_nth():
    reset(fail_every_nth=2)
    p = prov()
    await p.stream(MSG, 50)
    with pytest.raises(ServerError):
        await p.stream(MSG, 50)
    await p.stream(MSG, 50)
    await p.aclose()


async def test_slow_response_hits_first_token_timeout():
    reset(ttft_base_ms=1500)
    p = prov()
    with pytest.raises(ProviderTimeout):
        await p.stream(MSG, 50, first_token_timeout=0.3)
    await p.aclose()


async def test_latency_grows_with_load():
    reset(ttft_base_ms=100, load_knee=2, load_alpha=2.0)
    import asyncio
    p = prov()
    solo = (await p.stream(MSG, 20)).ttft_ms
    many = await asyncio.gather(*[p.stream(MSG, 20) for _ in range(10)])
    await p.aclose()
    assert max(c.ttft_ms for c in many) > 2 * solo


async def test_same_seed_same_behaviour():
    outs = []
    for _ in range(2):
        reset(seed=99, ttft_sigma=0.5, error_rate=0.3)
        p = prov()
        seq = []
        for _ in range(6):
            try:
                await p.stream(MSG, 50)
                seq.append("ok")
            except ServerError:
                seq.append("err")
        await p.aclose()
        outs.append(seq)
    assert outs[0] == outs[1] and "err" in outs[0]


async def test_limit_change_without_notice_is_visible_in_headers():
    reset()
    httpx.post(f"{BASE}/admin/config", json={"tpm": 4000})
    p = prov()
    c = await p.stream(MSG, 50)
    await p.aclose()
    assert c.headers["x-ratelimit-limit-tokens"] == "4000"


async def test_pipeline_answers_through_standin():
    reset()
    pipe = Pipeline(prov(), build_index(SETTINGS["A"]))
    rec = await pipe.run(Pipeline.intake("What is the frame rate of the Mimi codec?", 20))
    await pipe.provider.aclose()
    assert rec.status == "ok" and rec.decision in ("answer", "answer_partial") and "arXiv:2410.00037v1" in rec.answer
