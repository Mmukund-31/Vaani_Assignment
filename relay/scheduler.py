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
from .pipeline import Pipeline, RequestRecord


@dataclass
class SchedulerConfig:
    max_concurrency: int = 4
    max_queue: int = 40
    deadline_s: float = 20.0
    predictive_reject: bool = True


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

    def predicted_wait(self) -> float:
        """Queue wait for a new arrival = (people ahead / slots) x mean recent service time.
        Service time includes rate-limiter waits, so this tightens automatically when the provider limit binds."""
        if len(self.recent_service) < 5:
            return 0.0
        mean = sum(self.recent_service) / len(self.recent_service)
        return (self.waiting // self.cfg.max_concurrency + (1 if self.active >= self.cfg.max_concurrency else 0)) * mean

    async def handle(self, question: str, request_id: str | None = None, on_release=None) -> RequestRecord:
        rec = Pipeline.intake(question, self.cfg.deadline_s, request_id)
        # ---- admission
        if self.governor and self.governor.daily_exhausted():
            rec.status, rec.error_type, rec.decision = "rate_limited", "daily_budget", "reject_daily"
            reset_h = self.governor.day_reset_in() / 3600
            rec.answer = (f"Daily model budget is used up, so I can't answer new questions until it resets "
                          f"(in about {reset_h:.1f} h). Your question was not sent to the model.")
            return self._finish(rec)
        if self.waiting >= self.cfg.max_queue:
            rec.status, rec.error_type, rec.decision = "rate_limited", "queue_full", "reject_queue"
            return self._finish(rec)
        if self.cfg.predictive_reject and self.predicted_wait() > self.cfg.deadline_s * 0.8:
            rec.status, rec.error_type, rec.decision = "rate_limited", "predicted_wait", "reject_predicted"
            return self._finish(rec)
        # ---- queue + run, bounded by the person's patience
        try:
            await asyncio.wait_for(self._queued_run(rec, on_release), timeout=self.cfg.deadline_s)
        except asyncio.TimeoutError:
            rec.status, rec.error_type = "timeout", "user_gave_up"
            rec.total_ms = (time.monotonic() - rec.arrival) * 1000
            rec.answer = " ".join(rec.released)
            rec.ev("gave_up")
        return self._finish(rec)

    async def _queued_run(self, rec: RequestRecord, on_release):
        self.waiting += 1
        self.peak_queue = max(self.peak_queue, self.waiting)
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
