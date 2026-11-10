# Phase 0 interim results (2026-11-09)

First measurement of the engine on real open-source repos, as called for in `product-architecture.md` section 11.
**This is a small first pass, not the full Phase 0.** It proves the pipeline runs end to end on real code and gives
first cost numbers. It does **not** yet establish accuracy: nothing here was hand-scored.

## What was run

Each repo was cloned via `init <git url>`, then `ingest`, `extract`, `analyze --max-usd 1`. Work was done outside the repo
(scratch directory). Verifier was not run (no user docs supplied).

| Repo | Source lines (java/py/js) | Model config | Cost | Wall clock | LLM calls |
|---|---|---|---|---|---|
| spring-projects/spring-petclinic | 4,297 | gemini-flash3 (`gemini-3-flash-preview`) | $0.077 | 51 s | 2 |
| gothinkster/django-realworld-example-app | 1,778 | gemini-lite (`gemini-3.1-flash-lite`) | $0.021 | not timed | 2 |
| gothinkster/node-express-realworld-example-app | 2,374 | gemini-lite | $0.026 | not timed | 3 |

Why two configs: the flash3 run for the first repo succeeded, then the Gemini key hit its quota
(`429`, provider asked to wait about 24,800 s). The other two were rerun on the lite model so they could finish.
**The runs are therefore not like-for-like.** Cost and quality must not be compared across models from this table.

## What came out

| Repo | component | endpoint | data_entity | business_rule | external_dependency | config |
|---|---|---|---|---|---|---|
| spring-petclinic | 55 | 18 | 2 | 10 | 50 | 688 |
| django-realworld | 41 | 0 | 3 | 0 | 6 | 0 |
| node-express-realworld | 42 | 8 | 5 | 4 | 39 | 0 |

Confidence (derived from evidence): spring-petclinic 801 low / 22 medium; django 45 low / 5 medium;
node-express 90 low / 8 medium. **No node reached `high` in any repo.**

All three runs reported `coverage_complete: True`, `unmapped: 0`, `failed_chunks: 0`. Evidence validation rejected 2, 2
and 1 claims respectively, i.e. the evidence check is actively catching bad claims.

## Observations (honest reading)

1. **The pipeline works on unseen real code at very low cost** (cents per small repo). The unit-economics question is
   still open because these repos are tiny. Cost at 100k to 1M lines is not extrapolable from one chunk each.
2. **Most nodes are static facts, not model output.** In spring-petclinic, 688 of 823 nodes are config keys from
   extraction; the model contributed a small number (the log shows 24 for the single chunk). The product's value
   depends on the model-derived layer (rules, flows), which is the thin part.
3. **Django under-extraction: root cause found and fixed.** The django repo showed 0 endpoints because the static route
   extractor had no Django URLconf support (only Spring, Flask/FastAPI, Rails, ASP.NET, Express and a single-line
   `path()` regex). It missed `url(r'^...')` and DRF `router.register`. Added `extract_django` (with tests, and
   docstring/comment example filtering). After the fix the same repo yields 11 routes from static analysis alone,
   matching the application's API surface. Cross-file `include()` prefixes (`/api/`) are still not resolved.
   0 business rules from the lite model remains unexplained.
4. **Confidence is low almost everywhere.** Either the evidence bar is strict (good for trust) or the model is not
   supplying strong evidence (bad for usefulness). Needs a sample review to tell which.
5. **Everything ran as 1 chunk.** Chunking, cross-reference resolution, parallelism and rate-limit behaviour at scale
   are untested on real code. Those are where a hosted product will actually break.

## Not done yet (needed to finish Phase 0)

- Hand-score a sample of claims per repo for precision and recall against the source.
- Re-run all three on the same model to get comparable numbers (blocked by the Gemini daily quota; resets in about 7 hours, or supply another provider key).
- Larger repos (50k to 500k lines, several languages) to exercise chunking and measure cost per 1k lines.
- Run `verify` with real documentation to measure the Verifier.
- Investigate the Django 0-endpoint result.

Raw project folders are in the session scratchpad and will be lost; re-create them with the commands above.
