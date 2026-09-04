# PrizeScout — working notes for future sessions

## What this is
Finds giveaways on YouTube and extracts structured details (prize, deadline, how to enter).
FastAPI + SQLite backend (`src/`), Vite/React frontend (`frontend/`).

## The constraint that drives everything: YouTube quota
YouTube Data API v3 gives **10,000 quota units/day**, total, for the whole application.

| Call | Units |
|---|---|
| `search.list` | **100** |
| `videos.list` | 1 |
| `playlistItems.list` | 1 |
| `channels.list` | 1 |

`search.list` is 100x everything else. A single user query that paginates 3 pages of search
costs ~303 units — that is **33 user searches per day for the entire app**. Any design that
calls `search.list` per user request cannot ship.

**Architectural direction:** background ingestion over a curated channel watchlist
(`playlistItems.list` at 1 unit/call to walk channel uploads), writing into our own index.
Users query *our index*, never YouTube live. Do not add code that assumes a live per-request
YouTube call, and do not make that migration harder.

YouTube's API terms require cached API data to be refreshed or dropped on roughly a **30-day
cycle**. Storage must carry a `fetched_at` per row so stale rows can be found and expired.

## Hard rule: always persist raw text
`raw_description` must be stored on every row, always. The original DB stored only extracted
fields, which meant every extractor change required re-spending API quota to re-evaluate.
Never write an extraction path that discards its input. Same for `confidence` and
`extractor_version` — without them there is no ranking signal and no way to attribute a
regression to a version.

## Phases
- **Phase 0 — clear the deck** (done): untrack `prizes.db`, one frontend only, `dateparser`
  in requirements, modern fastapi/sqlmodel, `datetime.now(timezone.utc)`, unique constraint
  on `video_id` + upsert, `.env.example`, no API-key logging.
- **Phase 1 — make extraction measurable** (done): raw text persisted; bounded-quota corpus
  collector; terminal labeling tool; eval harness with precision/recall/F1 + regression test;
  then extractor fixes measured before/after.
- **Phase 2+ — not started**: trust scoring, background watchlist ingestion, notifications,
  second platform. Do not start these without being asked.

**Priority is precision over recall.** A false positive costs far more user trust than a miss.
Target >= 0.90 precision on `is_giveaway`. Report the recall cost honestly; never trade
precision away to make recall look good.

## Running it
```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env        # then set YOUTUBE_API_KEY
.venv/bin/uvicorn src.main:app --reload      # http://localhost:8000
cd frontend && npm install && npm run dev    # http://localhost:5173, proxies /api
```

## Tests
```bash
.venv/bin/python -m pytest tests/ -q
```
`tests/test_eval_regression.py` fails if `is_giveaway` precision drops below the threshold in
`eval_thresholds.json`. That threshold is a ratchet — raise it when you improve, and never
lower it to make a red build green.

## The measurement loop (this is the point of Phase 1)
```bash
python scripts/collect_corpus.py --max-units 3000   # ONCE. spends real quota. -> data/corpus.jsonl
python scripts/label.py                             # human labels. resumable. -> data/labels.jsonl
python scripts/eval.py                              # precision/recall/F1 + worst false positives
python scripts/calibrate.py                         # refit confidence weights on a holdout split
```
`eval.py` falls back to `tests/fixtures/seed_labels.jsonl` when no real labels exist. Those
seed fixtures are saturated (the extractor was iterated against them), so treat their 1.0 as
"the documented v1 bugs are fixed", never as a precision estimate. `calibrate.py` refuses to
fit on fewer than 60 labels and reports HOLDOUT numbers, never numbers from the training
split — a few hundred labels against hand-designed features overfits easily.
`data/` is gitignored — the corpus is API-cached content and must not be committed (30-day
rule), and it is large. Collect it once; after that, extractor iteration is free forever.
Never tune the extractor by re-running live searches.

## Gotchas
- Keyword lists must use word-boundary regexes. `"win" in text` matches *viewing, twin,
  window, rewind, drawing, swing, winter* — this is how an attorney-client disclaimer got
  stored as a PRIZE. Every keyword list is precompiled in `src/extractor.py`; add to the
  lists, not to ad-hoc `in` checks.
- YouTube descriptions are full of SEO keyword dumps and hashtag walls. They are not
  sentences and must be rejected before extraction, not after.
- `frontend/vite.config.mjs` is the only vite config and deliberately omits
  `@vitejs/plugin-react` (esbuild handles JSX; no Fast Refresh). Don't re-add a `.js` twin.
