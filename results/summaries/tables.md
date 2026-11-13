## Table 2a

| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) | Latency samples (n) |
|---|---|---|---|---|---|---|---|
| Steady (real Groq) | real | not measured | | | | | |
| Steady | stand-in | 15 | 281 | 3.0 | 851 / 1036 / 1036 | 1221 / 1651 / 1693 | 15 |
| Sudden burst | stand-in | 30 | 8 | 366.0 | 1036 / 6572 / 7346 | 1346 / 7014 / 7784 | 4 |
| Sustained overload | stand-in | 60 | 181 | 20.0 | 9472 / 12310 / 13250 | 9910 / 12498 / 13635 | 22 |
| Per-minute limit halved | stand-in | 24 | 243 | 6.0 | 5443 / 12542 / 12703 | 5726 / 12800 / 12886 | 14 |
| Daily budget exhausted | stand-in | not measured | | | | | |
| Retry: immediate | stand-in | not measured | | | | | |
| Retry: backoff+jitter | stand-in | not measured | | | | | |

## Table 2b

| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Error % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers (list-price equiv.) |
|---|---|---|---|---|---|---|---|---|
| Steady (real Groq) | not measured | | | | | | | |
| Steady | 100.0 | 0.0 | 0.0 | 0.0 | 0.00 | 1 | 2251 | $0.422 |
| Sudden burst | 13.3 | 86.7 | 0.0 | 0.0 | 0.00 | 1 | 2056 | $0.389 |
| Sustained overload | 36.7 | 63.3 | 0.0 | 0.0 | 0.00 | 1 | 1425 | $0.299 |
| Per-minute limit halved | 58.3 | 41.7 | 0.0 | 0.0 | 0.00 | 1 | 1394 | $0.306 |
| Daily budget exhausted | not measured | | | | | | | |
| Retry: immediate | not measured | | | | | | | |
| Retry: backoff+jitter | not measured | | | | | | | |

## Table 3

| Setting (piece size, pieces fetched, re-rank) | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Refused wrongly % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
|---|---|---|---|---|---|---|---|---|
| A: 300tok/k3/rerank=no | 18 | 50.0 | 11.1 | 16.7 | 22.2 | 1495 | 695 / 3780 | 806 / 3780 |
| C: 600tok/k4/rerank=yes | 18 | 72.2 | 11.1 | 16.7 | 0.0 | 2402 | 786 / 1439 | 839 / 1579 |
