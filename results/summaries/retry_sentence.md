at 150-360 requests/min ~8% of calls time out without retries; retrying recovers nearly all of them, but the
p95 of successful requests roughly doubles (2,063 → 3,848 ms at 360/min), because a retried person has already waited
a full 2.5 s timeout, and the server does 7-8% more calls. Backoff was no better than immediate retries (no
Retry-After, at most 2 retries, so there was no herd to spread out). A first 360/min run seemed to show a retry storm
(89% timeouts), but it had accidentally shared the stand-in with another run (`results/superseded/`); alone, there was none.
