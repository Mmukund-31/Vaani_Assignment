# The Relay

*Python 3.14 · Groq `openai/gpt-oss-120b`, free tier · runs 2026-11-13/14 · all tables generated from `results.csv`
by `scripts/make_tables.py` (Table 1 from `results/raw/groq_calibration_raw.jsonl`).*

## 1 What I built and why

**intake** (id, 15 s patience) → **search_papers** tool (BM25 over section-aware chunks with page/section metadata,
reference lists excluded) → **plan** (branch: refuse with no model call if nothing matches; loop: one extra per-paper
search for comparison questions; smaller context when tokens are short) → **generate** (one streamed call, passages
labelled `[P1..Pk]`) → **validate while streaming** (each sentence must cite a retrieved label and its numbers must
appear in that passage; only validated sentences are shown; one regeneration if the first fails). Code turns labels into
`[Title, arXiv:IDvN, p. X, Section Y]`, so a citation to a non-existent page cannot happen. A scheduler (bounded FIFO,
admission control) and a governor (RPM/TPM/RPD/TPD token buckets) sit in front of the model.
**The decision that shaped it most:** the bottleneck is the free tier's **8,000 tokens/minute**, not speed. Groq
answers in ~0.8 s, but an answer costs ~2,400 tokens, so the account serves ~3.3 answers/minute. Every other
choice follows from that: shed at arrival, shrink context rather than reject, never spend a second call casually.

## 2 The real service and my stand-in

**Table 1: Limits found in the real service (gpt-oss-120b, free tier, 2026-11-13/14)**

| Limit | Value found | How I found it | Confidence |
|---|---|---|---|
| Requests per minute | 30 | 429 body during a 34-request simultaneous burst: "requests per minute (RPM): Limit 30, Used 30"; 31 of 34 succeeded (bucket refilled ~1 request per 2 s during the burst) | high |
| Tokens per minute | 8,000 | `x-ratelimit-limit-tokens: 8000` on every response; two TPM 429s in the paced run ("Limit 8000, Used 4403, Requested 4662") | high |
| Requests per day | 1,000 | `x-ratelimit-limit-requests: 1000`; remaining fell by 1 per request and did not fall on a 429. Not exhausted | high (value), not reached |
| Tokens per day | 200,000 | Reached during the benchmark; 429 body "tokens per day (TPD): Limit 200000, Used 199068, Requested 1949". No header exposes it | high |
| Requests in flight at once | at least 34 (no limit hit) | 34 simultaneous requests all accepted, none rejected for concurrency; nothing in the docs. A lower bound, not the limit | low |
| What it returns when a limit is hit | HTTP 429 in 57-359 ms (slowest during the 34-request burst); JSON naming the limit, Limit / Used / Requested and "try again in Xs"; `retry-after` in whole seconds; `x-ratelimit-*` headers (RPM 429s omit the token headers) | 6 real 429s (2 TPM, 3 RPM, 1 TPD) logged with headers and bodies | high |
| Time until it recovers | Continuous refill: RPM ~2 s per request (recovered 2.5 s after the burst); TPM 133 tokens/s (retry-after 8 s and 14 s); TPD 2.31 tokens/s (a 1,017-token deficit took 7 min 19 s) | retry-after values and reset headers match deficit ÷ refill rate for every limit | high (RPM, TPM), medium (TPD: one data point) |


57 real calibration requests (51 ok, 6 × 429): 16 paced streams at two prompt sizes, a 34-request burst, a
`max_tokens` test, and probes after the daily budget ran out mid-benchmark. Every limit is a **continuously refilling
bucket**: each `retry-after`/reset value equals deficit ÷ refill rate. **Stand-in** (`standin/server.py`): same wire
format, headers and 429 bodies; TTFT = (404 ms + 0.102 ms × prompt tokens) × lognormal(σ 0.22), fitted to 491 ms @
850 and 682 ms @ 2,716 tokens; ~560 tokens/s in bursts; the same four buckets and admission charge (prompt +
min(max_tokens, 300)); seeded slow calls, stalls, 500s, broken streams and an in-flight cap. **Least like Groq:**
slowdown under load. Groq barely slowed (34 simultaneous tiny requests: 360-630 ms vs ~410 ms alone); my curve and
"abandoned requests keep running" are assumptions. Stand-in text is copied from the retrieved passages, so stand-in
rows are not graded.

## 3 Results

**Table 2a: Latency**

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

**Table 2b: Outcomes**

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

Percentiles use requests that showed an answer (n in 2a); the real steady row has 3, so its tails mean little.
First token = arrival → first *validated* sentence on screen, waits included. Cost = list price ($0.15/M in,
$0.60/M out); the free tier charged $0. Quality (Table 3, setting C): 72.2% correct and cited, all 3 unanswerable
refused, ~2,400 tokens per answer. **Numbers I could not have guessed:** the daily limit is a rolling bucket. After
it ran out, a 1,949-token request was told to wait 7 min 19 s, not until midnight: one answer about every 17
minutes. And `max_tokens` 2000 reserved only 298 output tokens at admission.

## 4 Where the time goes

Slowest real request, `groq-retrievalA-…-X3`: **15,658 ms, all before the first token** (first validated
sentence = last event). Trace: **working 3,460 ms** (retrieval 6 ms; call 1, 6.8 → 2,696.8 ms; call 2, 14,893.5 →
15,657.9 ms) + **waiting 12,196 ms** in our governor (2,696.8 → 14,893.5) + **retrying 0 ms of backoff**. But the
wait *was* the retry: call 1's draft failed validation, and the regeneration's token reservation did not fit the
minute's bucket. ~12.96 s of 15.66 s came from retrying, and it ended in a refusal of an answerable question.

## 5 When a limit is hit

**Per minute.** The governor keeps us under the limit before the provider has to say no: zero provider 429s in every stand-in run.
At arrival a request is **delayed** (FIFO) if its predicted token wait fits 80% of its patience; gets a **weaker answer**
(setting A, ~38% fewer tokens) if the full context would wait over 3 s and the small one would not (86% of answers in
sustained overload); or is **dropped at arrival in under 1 ms** otherwise. Early dropping is deliberate: my first version
queued everyone, and 22 of 30 burst requests waited 8-11 s only to be rejected. Headers re-tune limits that change
(halved run: TPM 8000 → 4000 learned from the first response). **Per day.** No header shows TPD, so the first daily 429's
body ("Limit X, Used Y") sets the bucket. From then on everyone is **dropped at arrival** with a capacity message and no model
call (stand-in: 1 provider 429, then 13 rejections at arrival; real: 1, then 8 without a model call). Nobody gets a
weaker *model* (section 8).

## Retrieval: quality versus cost

**Table 3: Retrieval settings (real Groq)**

| Setting (piece size, pieces fetched, re-rank) | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Refused wrongly % | Correct on answerable | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
|---|---|---|---|---|---|---|---|---|---|
| A: 300tok/k3/rerank=no | 18 | 50.0 | 11.1 | 16.7 | 22.2 | 9/15 | 1495 | 695 / 3780 | 806 / 3780 |
| B: 600tok/k4/rerank=no | 18 | 61.1 | 16.7 | 16.7 | 5.6 | 11/15 | 2340 | 842 / 1953 | 884 / 2049 |
| C: 600tok/k4/rerank=yes | 18 | 72.2 | 11.1 | 16.7 | 0.0 | 13/15 | 2402 | 786 / 1439 | 839 / 1579 |
| D: 900tok/k5/rerank=yes | 16 | 81.2 | 12.5 | 6.2 | 0.0 | 13/15 | 3746 | 697 / 925 | 798 / 1019 |

Same model, prompt, validator and rubric (`evaluation/grading_guidelines.md`, reasons in `grades.json`) for all
settings. Small pieces cause refusals (A wrongly refused 4 of 15 answerable questions); **re-ranking (B → C) adds 11
points for 3% more tokens and no model call.** Bigger pieces (D, 16 questions) got the same 13 of 15 answerable
questions right as C, but at 3,746 tokens per answer (+56%), which cuts the account to ~2 answers per minute and runs out
the daily budget sooner. Compare on the "correct on answerable" column: D's percentages have a different denominator.
**I chose C**, and under
token pressure I drop single requests to A (−22 points, −38% tokens) rather than make them wait or reject them. A's
errors are mostly honest refusals, not invented facts.

## How this library will mislead a naive retriever

From `results/summaries/naive_retriever.md` (BM25 top-4, references kept, one query).
**1. Shared vocabulary:** "What BLEU does Moshi get on WMT 2014?" returns four Attention BLEU passages, an easy path
to "28.4". Per-paper routing adds Moshi passages, and every setting refused correctly. **2. One word, three meanings:**
"What compression ratio does the model achieve?" could mean H-Net chunking, Mimi's 1.1 kbps, or quantized Moshi; both
retrievers return only H-Net. *Not mitigated*; it needs a clarifying question. **3. Bibliographies and comparisons:** a
reference entry ranks 4th for "who proposed layer normalization" (fixed by excluding references), and "what does each
of the three models consume" returns only H-Net; per-paper search restores coverage but finds Moshi's Related Work
section, so X3 was still wrong in C.

## 6 Retries

≤2 retries, exponential backoff with full jitter (0.5 s base, 8 s cap), never shorter than `retry-after`; a 429
pauses all workers; never on daily 429/400/401, within 1 s of the deadline, or after a sentence was shown; at most one
regeneration. **Worse, real:** X3 above, where the regeneration cost a 12.2 s wait and still ended in a refusal. **Worse,
stand-in sweep** (2.5 s first-token timeout): at 150-360 requests/min ~8% of calls time out without retries; retrying recovers nearly all of them, but the
p95 of successful requests roughly doubles (2,063 → 3,848 ms at 360/min), because a retried person has already waited
a full 2.5 s timeout, and the server does 7-8% more calls. Backoff was no better than immediate retries (no
Retry-After, at most 2 retries, so there was no herd to spread out). A first 360/min run seemed to show a retry storm
(89% timeouts), but it had accidentally shared the stand-in with another run (`results/superseded/`); alone, there was none.

**Retry sweep (stand-in)**

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

## 7 What the person sees

At 3 pm the daily budget is gone. The question returns in 0.1 ms with, verbatim from the real run:
*"Daily model budget is used up, so I can't answer new questions until it refills (next capacity in about 5 min).
Your question was not sent to the model."* No partial or unverified text and no other model; the minutes come from
the measured refill rate. Anyone already streaming still gets their validated sentences.
**[STUDENT: decide whether also to show the matching pages from retrieval, which needs no model, as "where to look".]**

## 8 What I'm unsure about, what I left out, what I learned

**Least certain:** that Groq reserves only ~300 output tokens at admission and does not slow under concurrency (4 data
points, one 34-request burst). If it reserves `max_tokens`, my governor under-reserves by up to 100 tokens per call
and meets 429s near the limit. If Groq slows under load, my overload and retry findings describe my stand-in, not
Groq. **Not built, on purpose:** (1) falling back to another model when the budget is gone, because it would change
answer quality without anyone measuring it; (2) an answer cache for burst repeats, which would add the most capacity but
could send one wrong answer to many people. **[STUDENT: one thing new to you, in your own words.]**

## Your feedback on Vaani

**[STUDENT: your own view after visiting https://www.vaani.dev/; factual notes in `docs/vaani_site_notes.md`.]**

## How I worked with the assistant

**[STUDENT: attach AI_CONVERSATION_HISTORY.md and write the half page: three prompts that mattered, one thing it got
wrong that you caught, one decision you overruled, what is distinctly yours. Logged candidates: PROGRESS.md
"Issues found and fixed" and results/superseded/README.md.]**
