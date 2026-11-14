# AI Usage Summary — The Relay

## 1. How I used the coding assistant

I used Claude Code as an AI coding assistant throughout the development of The Relay. I directed the work using a detailed assignment-specific master prompt, followed by smaller prompts to implement, debug, test and evaluate individual components.

The assistant was used for repository setup, Python implementation, paper ingestion, retrieval design, Groq integration, simulator development, rate-limit handling, experiment orchestration and report generation.

I reviewed the generated code, executed the experiments, inspected outputs and made the final engineering decisions. The complete chronological conversation export is provided in `AI_CONVERSATION_HISTORY.md`.

## 2. Three important prompts

**Prompt 1 — Assignment-wide engineering specification**

I provided the complete assignment brief and a master prompt specifying the mandatory pipeline, exact paper versions, real Groq integration, simulator capabilities, experiments, results schema and submission requirements.

Why it mattered: It established traceable acceptance criteria and prevented development from becoming an ordinary RAG chatbot implementation.

**Prompt 2 — Diagnose and improve retrieval**

I directed the assistant to investigate retrieval failures using the evaluation questions, paper passages and per-paper retrieval results. The resulting work included section-aware chunking, exclusion of reference lists, cross-paper retrieval and reranking experiments.

Why it mattered: It moved retrieval improvements from intuition to measurable comparisons across configurations A–D.

**Prompt 3 — Diagnose rate-limit and load-test failures**

I directed the assistant to examine actual provider response headers, rate-limit errors, queue behaviour and contaminated simulator experiments, then correct the corresponding implementation and rerun the experiments.

Why it mattered: It led to changes in token-bucket accounting, arrival-time admission control, token-aware scheduling and experiment isolation.

## 3. An AI-generated issue I caught

During early experiment development, an interrupted load-test process left another runner active. The two experiments unintentionally shared the same simulator, contaminating the observed retry results and making the failure rate appear much worse than it actually was.

I identified the issue by examining the experiment logs and configuration records, noticed overlapping runs, and separated the contaminated results from the final measurements. The affected experiments were preserved under `results/superseded/` and the corrected experiments were rerun independently.

## 4. An engineering decision I made

I chose not to add another model provider as a fallback when the Groq daily budget is exhausted.

Although such a fallback could improve availability, it would change the model behaviour and answer quality, making the results less comparable. I chose to preserve the daily capacity boundary and return a transparent service-capacity response instead.

## 5. What was my contribution

My contribution was defining the assignment-specific engineering objectives, choosing and reviewing the architecture, evaluating retrieval configurations, directing debugging, examining real provider measurements, making the final trade-off decisions, and reviewing the experimental evidence.

The coding assistant accelerated implementation, but I treated its generated code, reasoning and proposed grades as material to inspect and verify rather than unquestioned results.

The complete conversation export is included separately so the development process remains transparent.