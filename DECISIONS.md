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
