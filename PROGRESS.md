# Progress

| Phase | Status | Evidence |
|---|---|---|
| 0 Audit / init | done | venv (Python 3.14.4), requirements.txt pinned, .gitignore excludes .env, git init |
| 1 Ingestion | done | `python -m relay.ingest`; papers/manifest.json: 3 PDFs, sha256, abs-page title + version check, title on PDF p1 all true |
| 2 Retrieval | done (offline) | 4 settings A-D; `python -m experiments.retrieval_recall` -> gold-page recall 0.87 for all settings, misses X2/X3 (cross-paper) for all |
| 4 Pipeline (mock) | done | 27/27 pytest pass (`python -m pytest -q`), ruff clean |
| 3 Groq calibration | **blocked: needs GROQ_API_KEY in .env** | script ready: experiments/calibrate.py |
| 5 Stand-in | not started | |
| 6 Scheduler under load | code done, load runner not started | relay/scheduler.py |
| 7 Experiments | not started | |
| 8 Report | not started | |
| 9 Submission audit | not started | |

## Issues found and fixed
- Heading detection missed Attention/H-Net sections (number and title are separate bold lines in those PDFs) -> pair bold number line with next bold line.
- Span joining dropped whitespace spans, gluing words ("3.2TheHelium...") -> join all spans, test boldness on non-blank spans only.
- Moshi appendix (pp. 53-66) and Attention appendix were labelled "References" and would have been dropped by the reference filter -> detect "Appendix X." and "Attention Visualizations".
- Run-in paragraph labels ("Discussion.", "Related Work.") were overriding the real section -> unnumbered names ending in "." are not headings.
- Mock provider read only the last message, so regeneration feedback broke it -> read all user messages.
