# Grading guidelines (written before grading)

Exactly one grade per answered request, using the brief's four labels.

| Grade | Rule |
|---|---|
| `correct` | Question is answerable from the library, AND every factual claim is supported by the papers, AND each claim cites a passage that actually contains it (at least one cited page is a gold page from `questions.json`, and no citation points to a passage that says something different), AND the answer covers what the question asked (all parts listed as required in the question's grading notes), AND it adds no unsupported claims. |
| `wrong` | Any unsupported or incorrect claim, a claim attributed to the wrong paper, a missing required part, or a citation that does not support the sentence it is attached to. Also: answering an unanswerable question with anything other than a refusal (including computing a number the paper does not state). A partial answer whose released sentences are all correct but which misses a required part is `wrong`. |
| `refused_correctly` | The question is NOT answerable from the library and the system said so (model refusal, no-evidence refusal, or "could not verify"). |
| `refused_wrongly` | The question IS answerable from the library and the system refused or said it could not verify. |

Notes
- "Correct and cited %" in Table 3 = share graded `correct` (the definition already requires a supporting citation).
- Requests that never produced an answer for operational reasons (rate-limited, timed out) are not graded. In the
  benchmark, an attempt refused for the daily budget is logged under scenario `retrieval_<setting>_budget_wait`
  (excluded from Table 3) and the same question is asked again once the budget refills (D/X3, D/U2, D/U3).
- Stand-in load rows are not graded: their text is synthesised from retrieved passages, so grading would
  measure the stand-in, not the system.
- Grader: the mechanical key-fact check in `grading_sheet.md` is only a reading aid. Grades are assigned by reading
  each answer against the reference answer and the cited passages, and every grade has a one-line reason in
  `grades.json`.

## Procedure actually followed
1. Guidelines above written before any answers were graded.
2. Draft grades: the assistant read each released answer against the question's reference answer and the cited
   passages (papers/processed/*.json, i.e. the exact PDF text), and wrote a one-line reason per grade.
3. Mechanical audit (2026-11-14) over all 72 graded answers: grade consistent with decision (answered vs refused)
   and with answerability; every `correct` cites a gold page; every citation carries the pinned arXiv version.
   One flag (D/H1, gold list incomplete) - left for the student's decision.
4. Final: the student reviews every grade, decides the borderline ones, and changes `_meta.status` to FINAL.
   Until then the grades - and every quality number derived from them - are drafts.

Known limit of the validator vs. the grades: the validator proves a citation *label* points to a retrieved passage
and that numbers appear in it; it does not prove the passage supports the sentence's meaning. Misattributions
(A/M2, B/M2) and unsupported elaborations (A/X1, B/X1, D/X1) passed the validator and were caught only by grading.
