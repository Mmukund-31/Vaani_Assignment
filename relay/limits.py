"""Client-side rate governor: keeps us under the provider's RPM / TPM / RPD / TPD *before* it says 429.

Semantics
- RPM/TPM: sliding 60 s window over reservations (time, tokens). A reservation is made at dispatch with an
  *estimate* (prompt tokens + expected output) and reconciled to provider-reported usage afterwards.
- RPD/TPD: counters since the last daily reset (configurable reset time; Groq's exact reset rule is unknown,
  so we also follow its `x-ratelimit-remaining-requests` header when it disagrees with us).
- A provider 429 sets a global pause (`paused_until`) so all workers back off together instead of each
  discovering the limit separately (avoids a thundering herd at the reset instant).
- Waiters acquire under an asyncio.Lock, which is FIFO-fair: the request at the head waits, the rest queue.
- Limits learned from headers override configured ones: limits "can change without notice".
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from .providers import parse_duration


class LimitReject(Exception):
    def __init__(self, kind: str, wait_s: float, msg: str = ""):
        super().__init__(msg or f"{kind}: would need to wait {wait_s:.1f}s")
        self.kind = kind          # 'daily_requests' | 'daily_tokens' | 'rpm' | 'tpm' | 'pause'
        self.wait_s = wait_s


@dataclass
class Reservation:
    t: float
    tokens: int
    entry: list = field(default_factory=list)  # [t, tokens] shared with the window deque
    reconciled: bool = False


class Governor:
    def __init__(self, rpm: int | None, tpm: int | None, rpd: int | None, tpd: int | None,
                 day_seconds: float = 86400.0, clock=time.monotonic):
        self.rpm, self.tpm, self.rpd, self.tpd = rpm, tpm, rpd, tpd
        self.day_seconds = day_seconds
        self.clock = clock
        self.window: deque[list] = deque()  # [t, tokens]
        self.day_start = clock()
        self.day_requests = 0
        self.day_tokens = 0
        self.paused_until = 0.0
        self.lock = asyncio.Lock()
        self.limit_changes: list[dict] = []
        self.provider_429s = 0

    # ----- accounting helpers -----
    def _roll(self, now: float) -> None:
        while self.window and now - self.window[0][0] >= 60.0:
            self.window.popleft()
        if now - self.day_start >= self.day_seconds:
            self.day_start = now
            self.day_requests = 0
            self.day_tokens = 0

    def day_reset_in(self) -> float:
        return max(0.0, self.day_seconds - (self.clock() - self.day_start))

    def daily_exhausted(self, est_tokens: int = 0) -> str | None:
        self._roll(self.clock())
        if self.rpd is not None and self.day_requests + 1 > self.rpd:
            return "daily_requests"
        if self.tpd is not None and self.day_tokens + est_tokens > self.tpd:
            return "daily_tokens"
        return None

    def _wait_needed(self, now: float, tokens: int) -> tuple[float, str]:
        waits = [(max(0.0, self.paused_until - now), "pause")]
        if self.rpm is not None and len(self.window) >= self.rpm:
            waits.append((self.window[len(self.window) - self.rpm][0] + 60.0 - now, "rpm"))
        if self.tpm is not None:
            used = sum(e[1] for e in self.window)
            if used + tokens > self.tpm:
                # earliest time enough tokens have aged out of the window
                excess = used + tokens - self.tpm
                freed = 0
                t_ok = now
                for e in self.window:
                    freed += e[1]
                    t_ok = e[0] + 60.0
                    if freed >= excess:
                        break
                waits.append((t_ok - now if freed >= excess else 60.0, "tpm"))
        w, kind = max(waits, key=lambda x: x[0])
        return max(0.0, w), kind

    # ----- public API -----
    async def acquire(self, tokens: int, deadline: float | None) -> tuple[Reservation, float]:
        """Wait (FIFO) until the request fits under every limit. Returns (reservation, waited_seconds).
        Raises LimitReject if the daily budget is gone or the wait would overrun `deadline` (monotonic)."""
        t_start = self.clock()
        async with self.lock:
            while True:
                now = self.clock()
                self._roll(now)
                kind = self.daily_exhausted(tokens)
                if kind:
                    raise LimitReject(kind, self.day_reset_in(), f"{kind} budget exhausted")
                wait, why = self._wait_needed(now, tokens)
                if wait <= 0:
                    entry = [now, tokens]
                    self.window.append(entry)
                    self.day_requests += 1
                    self.day_tokens += tokens
                    return Reservation(now, tokens, entry), now - t_start
                if deadline is not None and now + wait > deadline:
                    raise LimitReject(why, wait)
                await asyncio.sleep(min(wait, 0.25))

    def reconcile(self, res: Reservation, actual_tokens: int | None) -> None:
        if res.reconciled or actual_tokens is None:
            return
        delta = actual_tokens - res.tokens
        res.entry[1] = max(0, res.entry[1] + delta)
        self.day_tokens = max(0, self.day_tokens + delta)
        res.reconciled = True

    def refund(self, res: Reservation) -> None:
        """Request was rejected by the provider before doing work (e.g. 429): give tokens back, keep request count."""
        self.reconcile(res, 0)

    def on_429(self, retry_after: float | None, fallback: float = 2.0) -> None:
        self.provider_429s += 1
        self.paused_until = max(self.paused_until, self.clock() + (retry_after if retry_after else fallback))

    def observe_headers(self, h: dict) -> None:
        """Adopt the provider's view when it is stricter than ours (limits can change without notice)."""
        if not h:
            return
        now = self.clock()
        try:
            lim_rpd = int(h["x-ratelimit-limit-requests"]) if "x-ratelimit-limit-requests" in h else None
            lim_tpm = int(h["x-ratelimit-limit-tokens"]) if "x-ratelimit-limit-tokens" in h else None
            rem_rpd = int(h["x-ratelimit-remaining-requests"]) if "x-ratelimit-remaining-requests" in h else None
            rem_tpm = int(float(h["x-ratelimit-remaining-tokens"])) if "x-ratelimit-remaining-tokens" in h else None
        except ValueError:
            return
        if lim_rpd is not None and lim_rpd != self.rpd:
            self.limit_changes.append({"t": now, "rpd": [self.rpd, lim_rpd]})
            self.rpd = lim_rpd
        if lim_tpm is not None and lim_tpm != self.tpm:
            self.limit_changes.append({"t": now, "tpm": [self.tpm, lim_tpm]})
            self.tpm = lim_tpm
        if rem_rpd is not None and self.rpd is not None:
            self.day_requests = max(self.day_requests, self.rpd - rem_rpd)
        if rem_tpm is not None and self.tpm is not None and rem_tpm <= 0:
            reset = parse_duration(h.get("x-ratelimit-reset-tokens")) or 1.0
            self.paused_until = max(self.paused_until, now + reset)
