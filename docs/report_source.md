# The Relay

*Python 3.14 · Groq `openai/gpt-oss-120b`, free tier · runs 2026-11-13/14 · all tables generated from `results.csv`
by `scripts/make_tables.py` (Table 1 from `results/raw/groq_calibration_raw.jsonl`).*

## 1 What I built and why

**intake** (id, 15 s patience) → **search_papers** tool (BM25 over section-aware chunks with page/section metadata,
reference lists excluded) → **plan** (branch: refuse with no model call if nothing matches; loop: one extra per-paper
search for comparison questions; smaller context when tokens are short) → **generate** (one streamed call, passages
labelled `[P1..Pk]`) → **validate while streaming** (each sentence must cite a retrieved label and its numbers must
appear in that passage; only validated sentences are shown; one regeneration if the first fails). Code turns labels into
`[Title, arXiv:IDvN, p. X, Section Y]`, so a citation to a non-existent page cannot happen. What this does *not* prove
is that the passage supports the sentence's meaning: 5 graded answers passed the validator yet credited a claim to the
wrong subject or added an unsupported mechanism (A/M2, B/M2, A/X1, B/X1, D/X1); only grading catches those. A scheduler (bounded FIFO,
admission control) and a governor (RPM/TPM/RPD/TPD token buckets) sit in front of the model.
**The decision that shaped it most:** the bottleneck is the free tier's **8,000 tokens/minute**, not speed. Groq
answers in ~0.8 s, but an answer costs ~2,400 tokens, so the account serves ~3.3 answers/minute. Every other
choice follows from that: shed at arrival, shrink context rather than reject, never spend a second call casually.

## 2 The real service and my stand-in

{{TABLE_1}}

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

{{TABLE_2A}}

{{TABLE_2B}}

Percentiles use only requests that showed an answer (n in 2a); denominators for outcomes are all offered requests.
Tails are unreliable where n is small: real steady n=3 (its p95/p99 are interpolations between 3 points), burst n=4,
daily-exhausted n=6. "Rate-limited" splits into **provider 429s** (the model service said no) and **local rejections**
(our admission control or governor refused before calling it): outside the two daily-budget rows, every rejection was local.
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

**Per minute.** The governor keeps us under the limit before the provider has to say no: no per-minute 429 from the provider in any load run.
At arrival a request is **delayed** (FIFO) if its predicted token wait fits 80% of its patience; gets a **weaker answer**
(setting A, ~38% fewer tokens) if the full context would wait over 3 s and the small one would not (86% of answers in
sustained overload); or is **dropped at arrival in under 1 ms** otherwise. Early dropping is deliberate: my first version
queued everyone, and 22 of 30 burst requests waited 8-11 s only to be rejected. Headers re-tune limits that change
(halved run: TPM 8000 → 4000 learned from the first response). **Per day.** No header shows TPD, so the first daily 429's
body ("Limit X, Used Y") sets the bucket. From then on everyone is **dropped at arrival** with a capacity message and no model
call (stand-in: 1 provider 429, then 13 rejections at arrival; real: 1, then 8 without a model call). Nobody gets a
weaker *model* (section 8).

## Retrieval: quality versus cost

{{TABLE_3}}

Same model, prompt, validator and rubric (`evaluation/grading_guidelines.md`, reasons in `grades.json`) for all
settings. Small pieces cause refusals (A wrongly refused 4 of 15 answerable questions); **re-ranking (B → C) adds 11
points for 3% more tokens and no model call.** Bigger pieces (D) bought nothing: exactly C's grades (13 of 15 answerable,
all 3 unanswerable refused) at 3,682 tokens per answer (+53%, ~2.2 instead of ~3.3 answers per minute); D also hit the
daily limit mid-run and finished only after waiting for it to refill. **I chose C**: the same quality as D for a third
fewer tokens, and under
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
stand-in sweep** (2.5 s first-token timeout): {{RETRY_SENTENCE}}

{{TABLE_SWEEP}}

## 7 What the person sees

At 3 pm the daily budget is gone. The question returns in 0.1 ms with, verbatim from the real run:
*"Daily model budget is used up, so I can't answer new questions until it refills (next capacity in about 5 min).
Your question was not sent to the model."* No partial or unverified text and no other model; the minutes come from
the measured refill rate. Anyone already streaming still gets their validated sentences.

## 8 What I'm unsure about, what I left out, what I learned

**Least certain:** that Groq reserves only ~300 output tokens at admission and does not slow under concurrency (4 data
points, one 34-request burst). If it reserves `max_tokens`, my governor under-reserves by up to 100 tokens per call
and meets 429s near the limit; a sign of this already: during D's last daily wait our estimate was slightly below
Groq's admission charge, so the run woke 10 times a few seconds early and took 10 free daily 429s before U3 went through. If Groq slows under load, my overload and retry findings describe my stand-in, not
Groq. **Not built, on purpose:** (1) falling back to another model when the budget is gone, because it would change
answer quality without anyone measuring it; (2) an answer cache for burst repeats, which would add the most capacity but
could send one wrong answer to many people. **[STUDENT: one thing new to you, in your own words.]**

## Your feedback on Vaani

**[STUDENT - write in your own words after visiting https://www.vaani.dev/. Factual observations you may react to
(from `docs/vaani_site_notes.md`, fetched 2026-11-14): the homepage claims p90 ~450 ms while /developers says p50 600 ms
and p90 900-1200 ms; pricing shows every cost layer (telephony, STT, LLM, TTS, platform; about Rs 5.66/min in the
example); "Start free" on the homepage vs a prepaid wallet with no stated free tier on /pricing; no list of supported
languages; pay-as-you-go allows 2 concurrent calls. What stood out: ___ What confused me: ___ One thing I would change: ___]**

## How I worked with the assistant

Full exported conversation: `AI_CONVERSATION_HISTORY.md`.
**[STUDENT - half page, your own words; list only what you actually did.
Three prompts that mattered most, and why: 1. ___ 2. ___ 3. ___
One thing it got wrong that I caught, and how I verified it: ___
One decision where I overruled it: ___
What in this submission is distinctly mine: ___
Evidence trail of defects found during the build (by whom is recorded in the conversation): PROGRESS.md
"Issues found and fixed" and results/superseded/README.md.]**
