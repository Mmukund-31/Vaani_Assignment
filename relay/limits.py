"""Client-side rate governor: keeps us under the provider's RPM / TPM / RPD / TPD *before* it says 429.

Semantics are copied from what Groq was measured to do (results/groq_calibration.json):
- Every limit is a continuously refilling token bucket, not a fixed window. Evidence: reset-tokens equals
  tokens-used / (TPM/60) exactly, reset-requests after one request was 1m26.4s = 86400 s / 1000 RPD, and the
  429 "try again in 7.9875s" equals the token deficit / 133.3 tok/s.
- TPM is charged at admission with prompt + an output estimate (observed <= ~300 tokens even when
  max_tokens=2000), so we reserve prompt + min(max_tokens, 300) and reconcile to real usage afterwards.
- A 429 does not consume the daily request budget (remaining-requests unchanged), so we refund on 429.
- A provider 429 pauses *all* workers until Retry-After (avoids a thundering herd).
- The provider's remaining-* headers clamp our buckets down when it knows better (other clients on the same
  account, or limits changed without notice). Changed limit values are adopted and logged.
- Waiters acquire under an asyncio.Lock, which is FIFO-fair.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .providers import parse_duration

OUTPUT_RESERVATION_CAP = 300   # measured: Groq's admission charge for output never exceeded ~300 tokens


class LimitReject(Exception):
    def __init__(self, kind: str, wait_s: float, msg: str = ""):
        super().__init__(msg or f"{kind}: would need to wait {wait_s:.1f}s")
        self.kind = kind          # 'daily_requests' | 'daily_tokens' | 'rpm' | 'tpm' | 'pause'
        self.wait_s = wait_s


class Bucket:
    def __init__(self, capacity: float | None, period_s: float, clock):
        self.capacity = capacity
        self.period = period_s
        self.clock = clock
        self.level = capacity or 0.0
        self.t = clock()

    @property
    def rate(self) -> float:
        return (self.capacity or 0) / self.period

    def refill(self) -> None:
        now = self.clock()
        if self.capacity is not None:
            self.level = min(self.capacity, self.level + (now - self.t) * self.rate)
        self.t = now

    def wait_for(self, n: float) -> float:
        if self.capacity is None:
            return 0.0
        self.refill()
        if self.level >= n:
            return 0.0
        if n > self.capacity:
            return float("inf")
        return (n - self.level) / self.rate

    def take(self, n: float) -> None:
        if self.capacity is not None:
            self.refill()
            self.level -= n

    def give(self, n: float) -> None:
        if self.capacity is not None:
            self.refill()
            self.level = min(self.capacity, self.level + n)

    def set_capacity(self, cap: int) -> None:
        self.refill()
        self.capacity = cap
        self.level = min(self.level, cap)


@dataclass
class Reservation:
    tokens: int
    reconciled: bool = False


class Governor:
    def __init__(self, rpm: int | None, tpm: int | None, rpd: int | None, tpd: int | None,
                 day_seconds: float = 86400.0, daily_wait_tolerance_s: float = 0.0, clock=time.monotonic):
        self.clock = clock
        self.rpm_b = Bucket(rpm, 60.0, clock)
        self.tpm_b = Bucket(tpm, 60.0, clock)
        self.rpd_b = Bucket(rpd, day_seconds, clock)
        self.tpd_b = Bucket(tpd, day_seconds, clock)
        # how long we are willing to wait for *daily* capacity before calling it exhausted (default: not at all)
        self.daily_wait_tolerance_s = daily_wait_tolerance_s
        self.paused_until = 0.0
        self.lock = asyncio.Lock()
        self.limit_changes: list[dict] = []
        self.provider_429s = 0
        self.pending_requests = 0      # callers currently inside acquire()
        self.pending_tokens = 0

    # convenience views
    @property
    def rpm(self):
        return self.rpm_b.capacity

    @property
    def tpm(self):
        return self.tpm_b.capacity

    @property
    def rpd(self):
        return self.rpd_b.capacity

    @property
    def tpd(self):
        return self.tpd_b.capacity

    @staticmethod
    def estimate(prompt_tokens: int, max_tokens: int) -> int:
        return prompt_tokens + min(max_tokens, OUTPUT_RESERVATION_CAP)

    def daily_exhausted(self, est_tokens: int = 1) -> str | None:
        if self.rpd_b.wait_for(1) > self.daily_wait_tolerance_s:
            return "daily_requests"
        if self.tpd_b.wait_for(est_tokens) > self.daily_wait_tolerance_s:
            return "daily_tokens"
        return None

    def daily_capacity_back_in(self, est_tokens: int = 1500) -> float:
        """Seconds until one typical request fits in the daily buckets again (they refill continuously)."""
        return max(self.rpd_b.wait_for(1), self.tpd_b.wait_for(est_tokens))

    def _wait_needed(self, tokens: int) -> tuple[float, str]:
        now = self.clock()
        waits = [(max(0.0, self.paused_until - now), "pause"),
                 (self.rpm_b.wait_for(1), "rpm"), (self.tpm_b.wait_for(tokens), "tpm")]
        return max(waits, key=lambda x: x[0])

    async def acquire(self, tokens: int, deadline: float | None) -> tuple[Reservation, float]:
        """Wait (FIFO) until the request fits every bucket. Returns (reservation, waited_seconds).
        Raises LimitReject if the daily budget is gone or the wait would overrun `deadline` (monotonic)."""
        t_start = self.clock()
        self.pending_requests += 1
        self.pending_tokens += tokens
        try:
            return await self._acquire(tokens, deadline, t_start)
        finally:
            self.pending_requests -= 1
            self.pending_tokens -= tokens

    async def _acquire(self, tokens: int, deadline: float | None, t_start: float) -> tuple[Reservation, float]:
        async with self.lock:
            while True:
                kind = self.daily_exhausted(tokens)
                if kind:
                    raise LimitReject(kind, self.daily_capacity_back_in(tokens), f"{kind} budget exhausted")
                wait, why = self._wait_needed(tokens)
                if wait <= 0:
                    for b, n in ((self.rpm_b, 1), (self.tpm_b, tokens), (self.rpd_b, 1), (self.tpd_b, tokens)):
                        b.take(n)
                    return Reservation(tokens), self.clock() - t_start
                if deadline is not None and self.clock() + wait > deadline:
                    raise LimitReject(why, wait)
                await asyncio.sleep(min(wait, 0.25))

    async def wait_until_fits(self, tokens: int) -> float:
        """Pacing helper for benchmarks: sleep until a request of `tokens` would be admitted without waiting."""
        t0 = self.clock()
        while True:
            if self.daily_exhausted(tokens):
                raise LimitReject("daily", self.daily_capacity_back_in(tokens))
            wait, _ = self._wait_needed(tokens)
            if wait <= 0:
                return self.clock() - t0
            await asyncio.sleep(min(wait, 0.5))

    def mark_daily_exhausted(self, kind: str | None = None, limit: int | None = None, used: int | None = None) -> None:
        """Provider told us (429 body) that a per-day limit is gone. No header exposes TPD, so the body is how we
        learn its real value: adopt it and set the bucket to what the provider says is left."""
        b = self.rpd_b if kind == "RPD" else self.tpd_b
        if limit is not None and limit != b.capacity:
            self.limit_changes.append({"t": self.clock(), (kind or "TPD").lower(): [b.capacity, limit], "source": "429 body"})
            b.set_capacity(limit)
        if b.capacity is None:
            b.capacity = 1
        b.refill()
        b.level = min(b.level, float(limit - used) if limit is not None and used is not None else 0.0)

    def predicted_wait(self, extra_requests: int, tokens_each: int) -> float:
        """Seconds until `extra_requests` more requests of `tokens_each` (queued behind everyone already waiting
        in acquire()) would all be admitted, from current bucket levels and refill rates."""
        now = self.clock()
        waits = [max(0.0, self.paused_until - now)]
        if self.tpm_b.capacity:
            self.tpm_b.refill()
            need = self.pending_tokens + extra_requests * tokens_each - self.tpm_b.level
            waits.append(max(0.0, need) / self.tpm_b.rate)
        if self.rpm_b.capacity:
            self.rpm_b.refill()
            need = self.pending_requests + extra_requests - self.rpm_b.level
            waits.append(max(0.0, need) / self.rpm_b.rate)
        return max(waits)

    def reconcile(self, res: Reservation, actual_tokens: int | None) -> None:
        if res.reconciled or actual_tokens is None:
            return
        delta = res.tokens - actual_tokens          # >0: we over-reserved, give back
        for b in (self.tpm_b, self.tpd_b):
            if delta > 0:
                b.give(delta)
            else:
                b.take(-delta)
        res.reconciled = True

    def refund(self, res: Reservation) -> None:
        """Provider rejected before doing work (429): tokens and the daily request come back (measured:
        remaining-requests unchanged after a 429). Whether a 429 counts toward RPM was not measured, so we
        conservatively keep the RPM charge."""
        self.reconcile(res, 0)
        self.rpd_b.give(1)

    def on_429(self, retry_after: float | None, fallback: float = 2.0) -> None:
        self.provider_429s += 1
        self.paused_until = max(self.paused_until, self.clock() + (retry_after if retry_after else fallback))

    def observe_headers(self, h: dict) -> None:
        if not h:
            return
        now = self.clock()

        def num(k):
            try:
                return int(float(h[k])) if k in h else None
            except ValueError:
                return None
        lim_rpd, lim_tpm = num("x-ratelimit-limit-requests"), num("x-ratelimit-limit-tokens")
        rem_rpd, rem_tpm = num("x-ratelimit-remaining-requests"), num("x-ratelimit-remaining-tokens")
        if lim_rpd is not None and lim_rpd != self.rpd_b.capacity:
            self.limit_changes.append({"t": now, "rpd": [self.rpd_b.capacity, lim_rpd]})
            self.rpd_b.set_capacity(lim_rpd)
        if lim_tpm is not None and lim_tpm != self.tpm_b.capacity:
            self.limit_changes.append({"t": now, "tpm": [self.tpm_b.capacity, lim_tpm]})
            self.tpm_b.set_capacity(lim_tpm)
        if rem_rpd is not None and self.rpd_b.capacity is not None:
            self.rpd_b.refill()
            self.rpd_b.level = min(self.rpd_b.level, rem_rpd)
        if rem_tpm is not None and self.tpm_b.capacity is not None:
            self.tpm_b.refill()
            self.tpm_b.level = min(self.tpm_b.level, rem_tpm)
            if rem_tpm <= 0:
                reset = parse_duration(h.get("x-ratelimit-reset-tokens")) or 1.0
                self.paused_until = max(self.paused_until, now + reset)
