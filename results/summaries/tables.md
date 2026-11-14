## Table 2a

| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) | Latency samples (n) |
|---|---|---|---|---|---|---|---|
| Steady, real Groq (daily token budget ran out mid-run) | real | 12 | 220 | 3.0 | 773 / 1122 / 1153 | 881 / 1238 / 1270 | 3 |
| Steady | stand-in | 15 | 281 | 3.0 | 861 / 1026 / 1035 | 1218 / 1657 / 1686 | 15 |
| Sudden burst | stand-in | 30 | 8 | 366.0 | 1066 / 6426 / 7157 | 1306 / 6818 / 7587 | 4 |
| Sustained overload | stand-in | 60 | 181 | 20.0 | 9461 / 12297 / 13251 | 9921 / 12473 / 13635 | 22 |
| Per-minute limit halved | stand-in | 24 | 243 | 6.0 | 5438 / 12528 / 12684 | 5722 / 12782 / 12870 | 14 |
| Daily budget exhausted | stand-in | 20 | 285 | 4.0 | 905 / 967 / 972 | 1184 / 1554 / 1619 | 6 |
| Retry: none (90/min) | stand-in | 180 | 121 | 90.0 | 868 / 1190 / 1315 | 1128 / 1631 / 1832 | 165 |
| Retry: immediate (90/min) | stand-in | 180 | 120 | 90.0 | 896 / 3323 / 3516 | 1184 / 3432 / 3789 | 180 |
| Retry: backoff+jitter (90/min) | stand-in | 180 | 120 | 90.0 | 887 / 3283 / 3686 | 1132 / 3494 / 4033 | 180 |

## Table 2b

| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Error % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers (list-price equiv.) | Degraded answers % |
|---|---|---|---|---|---|---|---|---|---|
| Steady, real Groq (daily token budget ran out mid-run) | 25.0 | 75.0 | 0.0 | 0.0 | 0.00 | 1 | 1895 | $0.342 | 0.0 |
| Steady | 100.0 | 0.0 | 0.0 | 0.0 | 0.00 | 1 | 2251 | $0.422 | 0.0 |
| Sudden burst | 13.3 | 86.7 | 0.0 | 0.0 | 0.00 | 2 | 2056 | $0.389 | 25.0 |
| Sustained overload | 36.7 | 63.3 | 0.0 | 0.0 | 0.00 | 2 | 1425 | $0.299 | 86.4 |
| Per-minute limit halved | 58.3 | 41.7 | 0.0 | 0.0 | 0.00 | 1 | 1394 | $0.306 | 85.7 |
| Daily budget exhausted | 30.0 | 70.0 | 0.0 | 0.0 | 0.00 | 1 | 2410 | $0.445 | 0.0 |
| Retry: none (90/min) | 91.7 | 0.0 | 8.3 | 0.0 | 0.00 | 1 | 2356 | $0.419 | 0.0 |
| Retry: immediate (90/min) | 100.0 | 0.0 | 0.0 | 0.0 | 0.09 | 1 | 2340 | $0.413 | 0.0 |
| Retry: backoff+jitter (90/min) | 100.0 | 0.0 | 0.0 | 0.0 | 0.07 | 1 | 2341 | $0.414 | 0.0 |

## Table 3

| Setting (piece size, pieces fetched, re-rank) | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Refused wrongly % | Correct on answerable | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
|---|---|---|---|---|---|---|---|---|---|
| A: 300tok/k3/rerank=no | 18 | 50.0 | 11.1 | 16.7 | 22.2 | 9/15 | 1495 | 695 / 3780 | 806 / 3780 |
| B: 600tok/k4/rerank=no | 18 | 61.1 | 16.7 | 16.7 | 5.6 | 11/15 | 2340 | 842 / 1953 | 884 / 2049 |
| C: 600tok/k4/rerank=yes | 18 | 72.2 | 11.1 | 16.7 | 0.0 | 13/15 | 2402 | 786 / 1439 | 839 / 1579 |
| D: 900tok/k5/rerank=yes | 16 | 81.2 | 12.5 | 6.2 | 0.0 | 13/15 | 3746 | 697 / 925 | 798 / 1019 |

## Retry sweep (stand-in)

| Offered (per min) | Retry policy | Requests | ok % | Timed out % | p50 / p95 total (ms) | Retries per request | Model calls sent per request |
|---|---|---|---|---|---|---|---|
| 150 | none | 150 | 92.7 | 7.3 | 1127 / 1639 | 0.00 | 1.00 |
| 150 | immediate | 150 | 99.3 | 0.7 | 1140 / 3375 | 0.09 | 1.09 |
| 150 | backoff | 150 | 99.3 | 0.7 | 1140 / 3639 | 0.09 | 1.09 |
| 240 | none | 240 | 92.5 | 7.5 | 1158 / 1624 | 0.00 | 1.00 |
| 240 | immediate | 240 | 100.0 | 0.0 | 1171 / 3534 | 0.08 | 1.08 |
| 240 | backoff | 240 | 100.0 | 0.0 | 1214 / 4053 | 0.10 | 1.10 |
| 360 | none | 360 | 91.9 | 8.1 | 1357 / 2063 | 0.00 | 1.00 |
| 360 | immediate | 360 | 100.0 | 0.0 | 1472 / 3848 | 0.08 | 1.08 |
| 360 | backoff | 360 | 100.0 | 0.0 | 1457 / 3952 | 0.07 | 1.07 |

## Slowest real requests

| request_id | total | first token | queue wait | limiter wait | retry wait | working (retrieval+model) | model calls |
|---|---|---|---|---|---|---|---|
| groq-retrievalA-20261113T232408-X3 | 15657.9 | 15657.9 | 0.0 | 12195.8 | 0.0 | 3460.0 | 2 |
| groq-retrievalC-20261113T232408-M1 | 2430.7 | 2390.1 | 0.0 | 0.0 | 0.0 | 2429.0 | 1 |
| groq-retrievalB-20261114T000146-X2 | 2130.8 | 2049.0 | 0.0 | 0.0 | 0.0 | 2129.5 | 1 |
