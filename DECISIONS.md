# Design decisions (living document)

## D1. Model cites passage labels, code renders citations
The model sees passages labelled [P1..Pk] and cites labels only. Code maps labels to
`[Title, arXiv:IDvN, p. X, Section Y]` from chunk metadata. Alternative: let the model write citations.
Rejected because a model can invent a page/section; with labels the validator only checks label membership,
and every rendered citation is real by construction. Cost: the model can still cite the *wrong* real passage;
the numeric-grounding check catches part of that, not all.

## D2. Sentence-gated streaming (validate while streaming)
Validation runs per sentence as tokens arrive; only validated sentences reach the person. Alternatives:
(a) stream raw tokens then validate (fast but can show wrong text), (b) buffer the whole answer then validate
(safe but TTFT = full generation time). Gate keeps TTFT near "first sentence" and never shows unverified text.
Cost: if a later sentence fails we cannot retract earlier ones, so we stop and say the rest was withheld; and
regeneration is only possible before anything was released. Reported TTFT = arrival -> first validated sentence.

## D3. Deterministic validation, not an LLM judge in the hot path
Checks: every sentence cites >=1 retrieved label; every number in the sentence appears in a cited passage
(or the question). Zero extra tokens/latency. An LLM verifier is to be measured once on the eval set before
deciding whether it earns its token cost under an 8K-TPM budget.

## D4. Planner is deterministic; retrieval is the tool; one bounded extra round
Measured offline: single-query BM25 misses the second paper for every comparison question at every setting
(recall 0.87, both misses are X2/X3). So the planner detects comparison / multi-paper questions (alias list
+ "compare/both/versus") and re-retrieves per paper (bounded: 1 extra round). It also refuses with zero model
calls when the best BM25 score < 4.0 (no lexical evidence at all).

## D5. Reference lists excluded from the index
Reference sections are full of other papers' titles containing "attention", "transformer", "speech" and
would win BM25 for the wrong reason. Appendices are kept (they hold real content, e.g. Moshi App. C).

## D6. Chunk unit = cl100k tokens, chunks never cross section boundaries
Approximate (Groq models use other tokenizers); provider-reported usage is what we record. Because sections
are short, the 600-token setting has a median chunk of ~470 tokens. Chunks under 25 tokens (heading-only) dropped.

## D7. Client-side governor + global pause on 429
Sliding 60 s window for RPM/TPM with estimate-then-reconcile reservations; RPD/TPD counters; FIFO lock;
a provider 429 pauses *all* workers until Retry-After; limits in response headers override configured
values (limits can change without notice).

## D8. Admission control sheds early
Order: daily budget -> queue full -> predicted wait > 80% of patience. Rejecting at arrival is cheaper and
kinder than queuing someone who will give up anyway. Cost: some requests that might have squeaked through are
rejected (measured in the load runs).

## D9. No HTTP front door for the pipeline (deliberate)
Pipeline runs in-process from CLI and load runner; the stand-in model is a separate FastAPI service.

## D10. Model: openai/gpt-oss-120b (verified 2026-11-13 via GET /models and response headers)
Free-tier limits confirmed for our account from headers: x-ratelimit-limit-requests=1000 (RPD),
x-ratelimit-limit-tokens=8000 (TPM); RPM=30 from a 429 body. TPD=200K from docs only (no header).
reasoning_effort=low: reasoning tokens averaged ~48 (medium prompts) and are billed + counted toward TPM.

## D11. Governor uses token buckets, not a sliding window (changed after measurement)
Initially a 60 s sliding window. Measurement showed Groq refills continuously: reset-tokens = used/(8000/60),
reset-requests = 86.4 s per request (= 86400/1000), retry-after = deficit/refill-rate. A sliding window would
leave capacity unused. Output reservation = min(max_tokens, 300) because Groq's admission charge for output
was measured at 5 / 152 / 298 / 300 tokens for max_tokens 5 / 1000 / 2000 / 300.

## D12. Token-aware admission (changed after the first burst smoke run)
First version queued everyone and let the governor reject when a request reached the head: 22/30 burst
requests waited 8-11 s and were then rejected. Now arrival-time prediction = (requests ahead + 1) x smallest
reservation vs current TPM bucket level and refill rate; shed immediately if > 80% of patience.
After the change the same burst sheds in <1 ms. Cost: prediction can be wrong (one request still waited 5.6 s
then was rejected because later arrivals consumed tokens it was counting on).

## D13. Degrade retrieval context instead of rejecting when TPM is the bottleneck
When the predicted TPM wait for the full context exceeds 3 s and the small context (setting A, ~1.46K vs
~2.40K tokens/answer measured) would be admitted sooner, use the small context. Who gets a weaker answer:
whoever arrives while the minute bucket is short. Quality cost = A vs C in Table 3.

## D14. Cost basis
Free tier charges $0. "Cost per 1,000 answers" = list-price equivalent at Groq's published gpt-oss-120b price
($0.15 / 1M input, $0.60 / 1M output; console.groq.com/docs/model/openai/gpt-oss-120b, fetched 2026-11-13).

## D15. Account identifiers redacted from logs
Groq 429 bodies contain the organization id; calibration and trace writers replace it with org_REDACTED.
