# Growing coverage

The ten-channel file is a bootstrap list, not a source ceiling. Background
discovery searches recent YouTube uploads across the categories in
`config/discovery.json`, fetches full descriptions, stores positive and negative
analysis, and registers the returned creators for upload polling. The merged
source list has no configured channel-count ceiling. Adding creators never
weakens the evidence or availability rules for displayed results.

Discovery runs as part of `scripts/ingest.py` and the optional embedded worker.
It can also run independently:

```bash
.venv/bin/python scripts/discover.py --dry-run
.venv/bin/python scripts/discover.py --max-units 64 --max-search-calls 16
.venv/bin/python scripts/ingest.py --max-units 200 --interval 3600
```

The combined worker reserves up to a third of its request allowance for discovery
and uses the remainder for source checks. Discovered sources join the same pass.
Existing least-recently-attempted polling and offer rechecks remain in effect.
`DISCOVERY_ENABLED=0` stops new query discovery while retained discovered sources
remain eligible for polling. `INGESTION_ENABLED=0` stops embedded background work
entirely; commands above are explicit independent runs.

Queries rotate by last attempt, with durable per-query pagination and a pinned
publication window. Completed queries have a six-hour cooldown. An incomplete
window restarts at the head after 24 hours so historical traversal cannot hide
new uploads indefinitely. The default lookback is 14 days. This is a bounded,
English-oriented candidate search, not an exhaustive listing of YouTube.
Search indexes can omit or delay videos; known sources use their uploads playlist
for follow-up. See Google's [search API documentation](https://developers.google.com/youtube/data/reference/rest/v3/search/list).

Successful detail fetches alone renew source timestamps. Offline replay cannot
renew checks or register fresh source observations. Cached query cursors and
discovered source metadata expire after 30 days; source deletion and video
retention also remove derived promotions. Individual source polling can retain
a source through successful new observations. Personal curated choices remain
in their own file.

## Request accounting

User search requests never contact YouTube. Background calls have per-pass and
persisted Pacific-day caps, including retries. Discovery uses a separate
`searchquotausage` ledger; channel, playlist and detail calls share `quotausage`.
The per-pass total includes both. By default discovery allows 8 searches per
combined pass and 80 per day. These are configurable safety budgets, not limits
on channel count or stored results. Google's current documentation describes a
default search bucket of 100 calls/day; verify the project's actual quotas before
raising budgets. [Official quota documentation](https://developers.google.com/youtube/data/determine_quota_cost)

Descriptions are persisted before a successful page checkpoint advances. A crash
replays an incomplete page safely. Authentication, malformed responses, outages,
quota exhaustion and timeout cannot renew unfetched sources. Discovery and source
polling use the same cross-process database lock.

## Database growth

SQLite uses WAL, a busy timeout and bounded atomic write batches. FTS and derived
giveaway/promotion records commit with their source batch. Composite indexes
support source-age checks, campaign closure checks and ranked reads. Search
results remain paginated; the source registry does not cap the result count.

`scripts/benchmark_scale.py` exercises the actual writer with disposable fictional
records, current-only and grouped queries, concurrent reading/writing, deletion,
SQLite integrity and external-content FTS integrity. Its report is a local
sequential workload, not a production concurrency or unlimited-capacity claim.
More users or concurrent writers may justify PostgreSQL and a job queue; that
migration should follow measured contention and latency.

The 2026-10-16 local run persisted 100,000 fictional source records, 1,000 positive
records and 200 promo mentions through the actual writer in 53.684 seconds
(1,862.74 rows/second), occupying 107.2 MB after checks. Six sequential query cases
with ten iterations each had p95 121.881–207.289 ms, including serialization.
There were 900 current fixture mentions / 450 current campaigns; the remaining
fixture positives included closure conflicts. Concurrent read/write, deletion of
100 sources and independent SQLite plus external-content FTS checks passed.

An earlier run returned an integrity warning on one pooled connection immediately
after deletion; independent connections and the explicit FTS check passed without
repair. The harness preserves any pooled warning and requires independent SQLite
checks before and after the stronger FTS external-content check. We have not
established a confirmed upstream SQLite bug. This check detects real FTS/source
drift in a deliberate corruption regression. See SQLite's
[integrity-check command](https://sqlite.org/fts5.html#the_integrity_check_command).
