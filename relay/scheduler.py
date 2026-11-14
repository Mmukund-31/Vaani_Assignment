"""Admission control + bounded FIFO queue + concurrency cap in front of the pipeline.

Admission order at arrival (cheapest check first, so overload is shed before it costs anything):
 1. daily budget gone?            -> reject now with a capacity message (status rate_limited, daily_budget)
 2. queue full?                   -> reject now (rate_limited, queue_full)
 3. predicted wait > deadline?    -> reject now (rate_limited, predicted_wait) instead of letting the person wait
                                     and give up anyway. Prediction = queue position / measured service rate.
Otherwise the request waits FIFO for a worker slot (asyncio.Semaphore is FIFO-fair). The person's patience
is the request deadline; when it passes the request is cancelled (status timeout, user_gave_up) wherever it is.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass

from .limits import Governor
from .pipeline import FAILED_NOTICE, Pipeline, RequestRecord, busy_notice


@dataclass
class SchedulerConfig:
    max_concurrency: int = 4
    max_queue: int = 40
    deadline_s: float = 20.0
    predictive_reject: bool = True
    min_tokens_per_request: int = 1300   # measured: ~1.0K prompt (setting A) + 300 output reservation


class Relay:
    def __init__(self, pipeline: Pipeline, cfg: SchedulerConfig, governor: Governor | None = None):
        self.pipeline = pipeline
        self.cfg = cfg
        self.governor = governor
        self.slots = asyncio.Semaphore(cfg.max_concurrency)
        self.waiting = 0
        self.active = 0
        self.peak_queue = 0
        self.recent_service: deque[float] = deque(maxlen=20)   # seconds per completed request

    def backlog(self) -> int:
        """Admitted requests not yet talking to the model: waiting for a worker slot + waiting in the governor."""
        return self.waiting + (self.governor.pending_requests if self.governor else 0)

    def predicted_limit_wait(self) -> float:
        """Rate-limit wait for a new arrival: everyone queued ahead plus this one, each charging the smallest
        reservation we could serve them with (degraded context if available)."""
        if not self.governor:
            return 0.0
        return self.governor.predicted_wait(self.waiting + 1, self.cfg.min_tokens_per_request)

    def predicted_wait(self) -> float:
        """Queue wait for a new arrival = (people ahead / slots) x mean recent service time.
        Service time includes rate-limiter waits, so this tightens automatically when the provider limit binds."""
        if len(self.recent_service) < 5:
            return 0.0
        mean = sum(self.recent_service) / len(self.recent_service)
        return (self.waiting // self.cfg.max_concurrency + (1 if self.active >= self.cfg.max_concurrency else 0)) * mean

    async def handle(self, question: str, request_id: str | None = None, on_release=None) -> RequestRecord:
        rec = Pipeline.intake(question, self.cfg.deadline_s, request_id)
        rec.queue_depth = self.backlog()
        # ---- admission
        if self.governor and self.governor.daily_exhausted(self.cfg.min_tokens_per_request):
            rec.status, rec.error_type, rec.decision = "rate_limited", "daily_budget", "reject_daily"
            back_min = self.governor.daily_capacity_back_in(self.cfg.min_tokens_per_request) / 60
            rec.answer = (f"Daily model budget is used up, so I can't answer new questions until it refills "
                          f"(next capacity in about {back_min:.0f} min). Your question was not sent to the model.")
            return self._finish(rec)
        if self.waiting >= self.cfg.max_queue:
            rec.status, rec.error_type, rec.decision = "rate_limited", "queue_full", "reject_queue"
            rec.answer = busy_notice(max(self.predicted_wait(), self.predicted_limit_wait(), 1.0))
            return self._finish(rec)
        predicted = max(self.predicted_wait(), self.predicted_limit_wait())
        if self.cfg.predictive_reject and predicted > self.cfg.deadline_s * 0.8:
            rec.status, rec.error_type, rec.decision = "rate_limited", "predicted_wait", "reject_predicted"
            rec.answer = busy_notice(predicted)
            return self._finish(rec)
        # ---- queue + run, bounded by the person's patience
        try:
            await asyncio.wait_for(self._queued_run(rec, on_release), timeout=self.cfg.deadline_s)
        except asyncio.TimeoutError:
            rec.status, rec.error_type = "timeout", "user_gave_up"
            rec.total_ms = (time.monotonic() - rec.arrival) * 1000
            rec.answer = " ".join(rec.released) or FAILED_NOTICE
            rec.ev("gave_up")
        return self._finish(rec)

    async def _queued_run(self, rec: RequestRecord, on_release):
        self.waiting += 1
        rec.queue_depth = self.backlog()
        self.peak_queue = max(self.peak_queue, self.backlog())
        rec.ev("queue_enter")
        t_q = time.monotonic()
        try:
            await self.slots.acquire()
        finally:
            self.waiting -= 1
            rec.queue_wait_ms = (time.monotonic() - t_q) * 1000
        rec.ev("queue_exit")
        self.active += 1
        t_s = time.monotonic()
        try:
            await self.pipeline.run(rec, on_release)
        finally:
            self.active -= 1
            self.slots.release()
            now = time.monotonic()
            if rec.status == "ok":
                self.recent_service.append(now - t_s)

    @staticmethod
    def _finish(rec: RequestRecord) -> RequestRecord:
        if rec.total_ms is None:
            rec.total_ms = (time.monotonic() - rec.arrival) * 1000
            rec.ev("done")
        return rec
