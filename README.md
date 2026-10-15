# PrizeScout

PrizeScout is a general-purpose YouTube giveaway index built with FastAPI, SQLite
FTS5, and React. It recognizes books, beauty, fashion, home goods, food, travel,
sports, crafts, pets, transport, electronics, games, cash, and gift cards.
Users search the local index; background ingestion handles YouTube requests.

Browse categories, search source text and extracted prizes, filter availability
and extraction score, and sort by score, publication date, or deadline. Source
links, creator names, publication dates and fetch dates help users check details.
Scores are experimental text signals. Listed promotions are not independently
verified, and an unknown deadline does not establish that entries remain open.

## Quick preview

Requires Python 3.13 and Node.js 22.12+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python scripts/demo.py
```

Open http://localhost:8000. The demo uses 18 clearly marked fictional examples in a
disposable database. It never writes to your real index or contacts YouTube.
`--port 8015` changes the port; `--check` validates the demo without serving it.

## Real data

Create `.env` from `.env.example` if it does not already exist, and set
`YOUTUBE_API_KEY`. Search and the web UI work without a key; ingestion requires one.

Use the optional researched starter watchlist:

```bash
cp -n config/watchlist.starter.json config/watchlist.json
.venv/bin/python scripts/ingest.py --dry-run
.venv/bin/python scripts/ingest.py --max-units 100 --daily-max-units 1000 --max-pages 1
.venv/bin/python scripts/serve.py
```

The copy preserves an existing personal watchlist. See
[starter sources](config/STARTER_CHANNELS.md) for provenance and limits. The list
spans ten channels; it does not promise a currently open offer in each category.
`--max-pages` bounds historical traversal per channel; durable cursors resume it on
later passes, while new uploads are checked at the head when budget permits.

Add your own channels by canonical ID or handle:

```bash
.venv/bin/python scripts/configure_watchlist.py --channel @your_handle --dry-run
.venv/bin/python scripts/configure_watchlist.py --channel @your_handle
```

Repeat `--channel` for multiple sources. Resolution costs one request per channel,
uses a persisted quota guard, and writes the file atomically after every input
resolves. Existing entries are retained; `--replace-existing` refreshes matching
metadata. You can edit `config/watchlist.json` directly with a `channels` list of
full `UC...` IDs or objects containing `channel_id` and optional
`uploads_playlist_id`. Personal configuration and downloaded inputs are ignored by
Git and Docker.

## Background ingestion

For an always-on single-server application:

```bash
INGESTION_ENABLED=1 .venv/bin/python scripts/serve.py
```

The optional worker runs immediately and then hourly, outside the HTTP event loop.
Configure its interval, quota, page and timeout caps in `.env` (all `INGESTION_*`
settings are documented in `.env.example`). `WATCHLIST_PATH` selects the source
list. The worker reports sanitized counters through logs and `/api/health`.

Alternatively, keep embedded ingestion off and run a separate worker:

```bash
.venv/bin/python scripts/ingest.py --interval 3600 --max-units 100 --daily-max-units 1000 --max-pages 1
```

Use one worker per database. A shared filesystem lock prevents the CLI and embedded
worker from racing. One bounded pass may finish during graceful shutdown; embedded
passes time out after 120 seconds by default. Daily accounting uses Pacific Time
and persists across restarts. Set caps below your actual Google project quota;
the local ledger cannot observe other applications sharing that project.

Current [Google quota documentation](https://developers.google.com/youtube/v3/determine_quota_cost)
describes separate search-call and other-endpoint budgets. Production ingestion
uses `channels.list`, `playlistItems.list`, and `videos.list`, each at one unit.
User searches spend zero YouTube requests. The offline collector retains its
conservative legacy-equivalent 100-unit search charge to bound research collection.

Raw descriptions are stored for every ingested video, including extraction
negatives. Sources are refreshed starting at 27 days and removed at 30 days on
maintenance passes; reads exclude stale rows immediately. Offline replay does not
renew fetch timestamps:

```bash
.venv/bin/python scripts/reextract.py --dry-run
.venv/bin/python scripts/reextract.py
```

## Development and checks

Run the API with `.venv/bin/uvicorn src.main:app --reload`, and use
`npm --prefix frontend run dev` for the frontend at http://localhost:5173. Vite
proxies `/api` to port 8000. A production build is served directly by FastAPI.

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/eval.py --json
.venv/bin/python scripts/demo.py --check
.venv/bin/python scripts/check_release.py
.venv/bin/python scripts/benchmark.py --iterations 50
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend audit --audit-level=high
```

GitHub Actions runs backend tests, seed evaluation, frontend tests/build, npm
auditing, and a container build/health/demo smoke test. No live API key is needed.

`check_release.py` uses disposable fictional data and fresh processes to rehearse
application restart and SQLite backup/restore. It checks full-text search, campaign
grouping, evidence, stale-source filtering, negative inputs, quota records and
ingestion cursors. `--skip-ui` runs without a frontend build. The real index and
YouTube are never used. Run this command on the hosted service too; passing locally
does not establish that the hosting disk and recovery procedure work.

`benchmark.py` opens the existing SQLite index in enforced read-only mode and
reports median/p95 timings for six fixed sequential query cases. Stop ingestion
while measuring. `--out data/benchmark.json` saves a new report without overwriting
an old one. This measures local database queries and serialization, not HTTP
concurrency or a representative user workload.

The API exposes `/api/search`, `/api/giveaways`, `/api/categories`, `/api/coverage`,
and `/api/health`.
Search parameters include `q`, `page`, `page_size` (1–100), `min_confidence` (0–1),
`category`, `availability=all|active|unknown|expired`, and
`sort=confidence|newest|deadline`. `all` excludes expired results. Responses contain
`results`, pagination metadata and `index_stats`. The API docs are at `/docs`.

The UI defaults to `group_campaigns=true`, combining videos with one identical
recognized giveaway entry link into one result before pagination. Toggle
**Group related videos** off for individual videos. Supported entry providers are
Gleam, Woobox, Rafflecopter, SweepWidget and KingSumo. Generic, shortened and
ambiguous links remain separate; no redirect or external lookup is performed.
This first version does not use title similarity or semantic campaign matching.
Category counts still count video mentions, so they may exceed grouped results.

Grouped results retain all fresh source videos, including sources that did not
match the search text or other filters. The leading details come from a matching
representative video, selected by the requested sort. Differences in reported
prizes, deadlines and entry steps are disclosed for comparison. A shared link
does not verify that an offer is open, authentic, or correctly described.

**Source evidence** shows exact matched text with title/description offsets (Python
string positions, rather than JavaScript UTF-16 positions) for
supported fields. Prize spans support names, not valuations. Missing evidence
means the system could not establish support; it never invents quotes. Existing
retained records gain campaign identities and evidence on the next ingestion
refresh or offline `scripts/reextract.py` run. Source deletion, stale filtering
and replay also apply to grouped results and their evidence.

Entry instructions require local giveaway context. A generic request to like,
subscribe or follow does not become an entry requirement merely because the
video title mentions a giveaway. Unsupported instructions stay blank.

## Coverage and human review

Expand **Index coverage** in the app to see fresh videos, detected mentions and
per-channel progress. These are inventory counts, not accuracy estimates. Legacy
rows without an analysis remain unknown until re-extracted. Rejected inputs retain
their decision, reason and extractor version alongside the source timestamp.

Inspect aggregate diagnostics and prepare a review packet without API calls:

```bash
.venv/bin/python scripts/diagnose.py
.venv/bin/python scripts/prepare_review.py --limit 100 --seed review-1 \
  --out-dir data/review-1
.venv/bin/python scripts/label.py --packet data/review-1 --reviewer YOUR_NAME
```

Diagnostics add rejection reasons, input coverage and the local quota ledger;
the public endpoint omits those operational details. Review packets contain source
snapshots, connected campaign/duplicate groups, stable suggested partitions and a
pinned annotation clock. Packet labeling hides model guesses and records explicit
human decisions and reviewer provenance. It checks source hashes and refuses
stale inputs. Existing export directories are never overwritten.

The sampler enriches channel and prediction buckets for diagnosis; it does not
produce a representative population estimate. Exports remain cached source data
under ignored `data/` and need the same retention lifecycle. See the
[labeling workflow](docs/LABELING.md) for the rubric, split policy and limitations.

## Deployment

The recommended initial deployment is one Render web service with a 1 GB
persistent disk. [render.yaml](render.yaml) configures the Docker build, health
check, port handling, ten-channel starter watchlist, and embedded ingestion
capped at 1,000 quota units/day. Auto-deploys are off until changes are reviewed.

Push the reviewed implementation to your Git repository, connect it as a Render
Blueprint, supply `YOUTUBE_API_KEY` as a secret environment variable, and confirm
the displayed hosting charges. The configured 0.5 CPU / 512 MB plan is paid;
check [current pricing](https://render.com/pricing) before creating it. The key is
never committed or bundled into frontend assets. Keep one service instance and
one Uvicorn worker: Render disks cannot be shared between services. The disk must
be writable by the container's `scout` user.

After deployment, verify `/api/health`, an ingestion pass, categories and searches,
then restart and confirm the index and quota ledger survive. Hosting is not
created just by adding `render.yaml`.

For local Docker hosting:

```bash
docker compose up --build -d web
# After setting .env and config/watchlist.json:
docker compose --profile ingestion up --build -d
```

Compose binds to localhost:8000 and shares the `scout-data` volume with its opt-in
CLI ingester. Put external access behind an HTTPS reverse proxy. Do not remove
the volume during upgrades. Docker is not installed in the development environment;
container runtime checks are defined in CI and still need a successful CI run.

Before upgrades, stop ingestion and take a consistent SQLite backup:

```bash
.venv/bin/python scripts/backup_db.py --out /path/to/backups/prizes.db
```

The backup command refuses overwrites. Restore with web and ingestion stopped,
retain the original, and verify `/api/health` afterward. Legacy migrations are
additive; duplicate rows are archived before consolidation. Missing fetch dates
become stale. Missing original descriptions cannot be recovered by migration.
Backups and local corpora also need a cache-retention policy.

## Measurement and next stages

Tests cover known failures and synthetic category examples. The 30 existing human
labels contain only four positives; their passing score is not a production
precision estimate. The 407 local agent annotations remain provisional. Resolve
review flags and promotion-versus-availability semantics before calibration, and
keep a separate campaign-grouped final test set. Calibration refuses provisional
labels and uses separate train/validation/test partitions.

See [PROJECT_STATUS.md](PROJECT_STATUS.md) for the current implementation checkpoint
and [the architecture](docs/ARCHITECTURE.md) for component responsibilities.
[ROADMAP.md](ROADMAP.md) describes the staged software engineering, data engineering,
and applied ML vision. Notifications, more platforms, and independent trust
assessment are future scope.
