# PrizeScout — working notes for future sessions

## Project direction

Build a coherent flagship project demonstrating software engineering, data
engineering and applied ML. Finish the deployable MVP, then evaluate improvements
in stages with reviewed data and measured results.

## What this is
Finds giveaways on YouTube and extracts structured details (prize, deadline, how to enter).
FastAPI + SQLite backend (`src/`), Vite/React frontend (`frontend/`).

## The constraint that drives everything: YouTube quota
YouTube quota is bounded across the application; user searches must query our index.
The original project assumed a shared **10,000-unit/day** budget and charged
`search.list` at 100 units. Google's current quota documentation (checked 2026-10-14)
describes a separate default bucket of **100 search calls/day**, with a 1-unit cost,
plus **10,000 units/day for other endpoints**. Check the project's actual Cloud Console
quotas before spending them: https://developers.google.com/youtube/v3/determine_quota_cost

| Call | Units |
|---|---|
| `search.list` | 1 in its own limited bucket; collector conservatively budgets 100 legacy-equivalent units |
| `videos.list` | 1 |
| `playlistItems.list` | 1 |
| `channels.list` | 1 |

The collector's existing 100-unit search charge remains a conservative local cap,
not a claim about current API billing. Do not claim a measured 100x saving from that
legacy assumption. Production ingestion uses the 1-unit channel/upload/detail APIs,
and every user search spends **zero YouTube requests** regardless of quota changes.

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
- **Phase 2 — MVP implementation**: local indexed search, background watchlist ingestion,
  availability handling, frontend filters/error states, CI and deployment packaging.
  User authorized this phase on 2026-10-14. Notifications, a second platform and
  independent trust/scam verification remain future scope.

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
python scripts/calibrate.py --now 2026-10-14T00:00:00Z # requires reviewed labels matching this clock
```
`eval.py` falls back to `tests/fixtures/seed_labels.jsonl` when no real labels exist. Those
seed fixtures are saturated (the extractor was iterated against them), so treat their 1.0 as
"the documented v1 bugs are fixed", never as a precision estimate. `calibrate.py` refuses to
fit on fewer than 60 labels and uses grouped train/validation/test splits and a pinned clock, never reporting training
performance as test quality — a few hundred labels against hand-designed features overfits easily.
`data/` is gitignored — the corpus is API-cached content and must not be committed (30-day
rule), and it is large. Reuse retained raw text for offline iteration without API calls; refresh or remove cached
source data on its retention schedule. Offline re-extraction must never reset fetched_at.
Never tune the extractor by re-running live searches.

## Gotchas
- The extractor takes **title and description**, and the title is deliberately *not*
  concatenated into the description: `short_text`, `hashtag_ratio` and `is_tag_dump` are
  ratios over the description, and prepending a title moves all three for every video.
  Title signals are their own features (`title_strong`, `title_prize_value`, `title_music`)
  and the title also feeds the negation/closed rejects, because widening what gets admitted
  is what makes a "GIVEAWAY WINNER ANNOUNCED" title dangerous. Title prizes are named items
  only — the `_money_label` fallback reads prose but turns "$1200 PC GIVEAWAY!" into the
  prize "PC GIVEAWAY". `title_only` on the result marks a detection the description alone
  would have missed. Nothing new needs persisting: `title` and `raw_description` are both
  stored, so any of this is recomputable without spending quota.
- Keyword lists must use word-boundary regexes. `"win" in text` matches *viewing, twin,
  window, rewind, drawing, swing, winter* — this is how an attorney-client disclaimer got
  stored as a PRIZE. Every keyword list is precompiled in `src/extractor.py`; add to the
  lists, not to ad-hoc `in` checks.
- YouTube descriptions are full of SEO keyword dumps and hashtag walls. They are not
  sentences and must be rejected before extraction, not after.
- `frontend/vite.config.mjs` is the only vite config and deliberately omits
  `@vitejs/plugin-react` (esbuild handles JSX; no Fast Refresh). Don't re-add a `.js` twin.
  Vite is pinned to 7.3.7; use Node.js 22.12+ and retain the explicit browser targets
  when upgrading. CI runs `npm audit --audit-level=high`.
- SQLite returns naive `fetched_at` values despite UTC writes. API responses must
  restore the UTC offset so browsers do not treat the timestamp as local time.
