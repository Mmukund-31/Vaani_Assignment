"""Model providers behind one interface.

`OpenAICompatProvider` speaks the OpenAI-style streaming chat API (SSE). The same class talks
to real Groq and to our stand-in service: the stand-in imitates Groq's wire format, status
codes and rate-limit headers, so the pipeline cannot tell them apart.
`MockProvider` is in-process and deterministic, for unit tests.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

RATE_HEADERS = ("x-ratelimit-limit-requests", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-requests",
                "x-ratelimit-remaining-tokens", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens",
                "retry-after")


# ---------- errors (classification drives the retry policy) ----------
class ProviderError(Exception):
    retryable = False
    status = "error"

    def __init__(self, msg: str, http_status: int | None = None, headers: dict | None = None,
                 retry_after: float | None = None):
        super().__init__(msg)
        self.http_status = http_status
        self.headers = headers or {}
        self.retry_after = retry_after


class RateLimited(ProviderError):
    """429. `daily=True` when the provider says the per-day budget is gone (not worth retrying)."""
    retryable = True
    status = "rate_limited"

    def __init__(self, msg, daily: bool = False, **kw):
        super().__init__(msg, **kw)
        self.daily = daily
        if daily:
            self.retryable = False


class ServerError(ProviderError):
    retryable = True


class ProviderTimeout(ProviderError):
    retryable = True
    status = "timeout"


class BadRequest(ProviderError):
    retryable = False


class AuthError(ProviderError):
    retryable = False


@dataclass
class Completion:
    text: str = ""
    ttft_ms: float | None = None          # first content token from the provider
    total_ms: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    reasoning_tokens: int | None = None
    http_status: int | None = None
    headers: dict = field(default_factory=dict)
    chunk_times_ms: list[float] = field(default_factory=list)
    model: str = ""


def parse_duration(v: str | None) -> float | None:
    """Groq reset headers look like '2m59.56s', '7.66s', '120ms'."""
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        pass
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)(ms|h|m|s)", v):
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total


def _classify(resp_status: int, body: str, headers: dict) -> ProviderError:
    ra = parse_duration(headers.get("retry-after"))
    low = body.lower()
    if resp_status == 429:
        daily = ("per day" in low) or ("tokens per day" in low) or ("requests per day" in low) or ("(rpd)" in low) or ("(tpd)" in low)
        return RateLimited(f"429: {body[:300]}", daily=daily, http_status=429, headers=headers, retry_after=ra)
    if resp_status in (401, 403):
        return AuthError(f"{resp_status}: {body[:200]}", http_status=resp_status, headers=headers)
    if resp_status in (400, 404, 413, 422):
        return BadRequest(f"{resp_status}: {body[:300]}", http_status=resp_status, headers=headers)
    return ServerError(f"{resp_status}: {body[:300]}", http_status=resp_status, headers=headers, retry_after=ra)


class Provider:
    name = "base"
    source = "real"

    async def stream(self, messages: list[dict], max_tokens: int, on_text: Callable[[str], None] | None = None,
                     first_token_timeout: float = 30.0, total_timeout: float = 60.0) -> Completion:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


class OpenAICompatProvider(Provider):
    def __init__(self, base_url: str, api_key: str, model: str, source: str, name: str,
                 reasoning_effort: str | None = None, max_connections: int = 64):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.source = source
        self.name = name
        self.reasoning_effort = reasoning_effort
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(connect=10, read=None, write=10, pool=None),
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections))

    async def aclose(self):
        await self.client.aclose()

    def _payload(self, messages, max_tokens):
        p = {"model": self.model, "messages": messages, "stream": True, "temperature": 0.0,
             "max_completion_tokens": max_tokens, "stream_options": {"include_usage": True}}
        if self.reasoning_effort and "gpt-oss" in self.model:
            p["reasoning_effort"] = self.reasoning_effort
            p["include_reasoning"] = False
        return p

    async def stream(self, messages, max_tokens, on_text=None, first_token_timeout=30.0, total_timeout=60.0):
        t0 = time.perf_counter()
        out = Completion(model=self.model)
        try:
            await asyncio.wait_for(self._run(messages, max_tokens, on_text, t0, out, first_token_timeout),
                                   timeout=total_timeout)
        except asyncio.TimeoutError as e:
            out.total_ms = (time.perf_counter() - t0) * 1000
            kind = "first token" if out.ttft_ms is None else "completion"
            err = ProviderTimeout(f"timeout waiting for {kind} after {out.total_ms:.0f} ms", headers=out.headers)
            err.partial = out
            raise err from e
        out.total_ms = (time.perf_counter() - t0) * 1000
        return out

    async def _run(self, messages, max_tokens, on_text, t0, out: Completion, first_token_timeout):
        req = self.client.build_request("POST", f"{self.base_url}/chat/completions",
                                        json=self._payload(messages, max_tokens))
        try:
            resp = await asyncio.wait_for(self.client.send(req, stream=True), timeout=first_token_timeout)
        except asyncio.TimeoutError as e:
            raise ProviderTimeout(f"no response headers within {first_token_timeout}s") from e
        except httpx.TransportError as e:
            raise ServerError(f"transport error: {type(e).__name__}: {e}") from e
        try:
            out.http_status = resp.status_code
            out.headers = {k: resp.headers[k] for k in RATE_HEADERS if k in resp.headers}
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise _classify(resp.status_code, body, out.headers)
            lines = resp.aiter_lines()
            while True:
                timeout = first_token_timeout - (time.perf_counter() - t0) if out.ttft_ms is None else None
                try:
                    line = await asyncio.wait_for(anext(lines), timeout=timeout) if timeout is not None else await anext(lines)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as e:
                    raise ProviderTimeout(f"no first token within {first_token_timeout}s") from e
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                obj = json.loads(data)
                if "error" in obj:
                    raise ServerError(f"stream error: {obj['error']}")
                for ch in obj.get("choices") or []:
                    piece = (ch.get("delta") or {}).get("content")
                    if piece:
                        now = (time.perf_counter() - t0) * 1000
                        if out.ttft_ms is None:
                            out.ttft_ms = now
                        out.chunk_times_ms.append(now)
                        out.text += piece
                        if on_text:
                            on_text(piece)
                usage = obj.get("usage") or (obj.get("x_groq") or {}).get("usage")
                if usage:
                    out.tokens_in = usage.get("prompt_tokens")
                    out.tokens_out = usage.get("completion_tokens")
                    det = usage.get("completion_tokens_details") or {}
                    out.reasoning_tokens = det.get("reasoning_tokens")
        except httpx.TransportError as e:
            raise ServerError(f"transport error mid-stream: {type(e).__name__}") from e
        finally:
            await resp.aclose()


class MockProvider(Provider):
    """Deterministic in-process provider for tests. `script` maps a call index to behaviour."""
    name = "mock"
    source = "stand-in"

    def __init__(self, responder: Callable[[list[dict], int], str] | None = None,
                 errors: dict[int, ProviderError] | None = None, delay: float = 0.0, chunk_delay: float = 0.0):
        self.responder = responder or default_mock_answer
        self.errors = errors or {}
        self.delay = delay
        self.chunk_delay = chunk_delay
        self.calls = 0
        self.model = "mock"

    async def stream(self, messages, max_tokens, on_text=None, first_token_timeout=30.0, total_timeout=60.0):
        i = self.calls
        self.calls += 1
        t0 = time.perf_counter()
        if i in self.errors:
            raise self.errors[i]
        if self.delay > min(first_token_timeout, total_timeout):
            await asyncio.sleep(min(first_token_timeout, total_timeout))
            raise ProviderTimeout("mock timeout")
        await asyncio.sleep(self.delay)
        text = self.responder(messages, i)
        out = Completion(model="mock", http_status=200)
        for w in re.findall(r"\S+\s*", text):
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)
            now = (time.perf_counter() - t0) * 1000
            out.ttft_ms = out.ttft_ms or now
            out.chunk_times_ms.append(now)
            out.text += w
            if on_text:
                on_text(w)
        out.tokens_in = sum(len(m["content"]) for m in messages) // 4
        out.tokens_out = max(1, len(text) // 4)
        out.total_ms = (time.perf_counter() - t0) * 1000
        return out


def default_mock_answer(messages: list[dict], i: int) -> str:
    """Answer with the first sentence of passage P1 (always grounded), or refuse if no passages."""
    user = "\n".join(m["content"] for m in messages if m["role"] == "user")
    m = re.search(r"\[P1\] \([^)]*\)\n(.+?[.!?])(\s|$)", user, re.S)
    if not m:
        return "NOT_IN_LIBRARY: The retrieved passages do not address this question."
    return f"{m.group(1).strip()} [P1]"
