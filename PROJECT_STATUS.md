# PrizeScout implementation status

Verified locally on 2026-10-15.

## Implemented

- FastAPI/React app with SQLite full-text search, categories, filters and pagination.
- Background watchlist ingestion, bounded retries/quota, durable cursors and cache lifecycle.
- General prize extraction, availability uncertainty, exact field evidence and shared-link campaign grouping.
- Public coverage summary and local aggregate operational diagnostics.
- Reproducible review packets, blind human labeling, source hashes, grouped partitions and pinned clocks.
- Evaluation/calibration safeguards; score weights remain hand-set and no trained model is deployed.
- Docker/Render packaging and CI; isolated restart/backup/restore rehearsal.
- Read-only local query benchmark and component architecture documentation.

## Measured evidence and limits

The local index retains 1,227 inputs across ten sources. Six mentions are detected,
all with unknown availability; none is independently verified open. A two-source
coverage experiment fetched 100 videos with six total quota units including
identity resolution and detected no additional mentions. Expanded prize rules
increased detections among existing sources. These counts do not establish accuracy.

358 backend tests and ten frontend tests pass. The production frontend builds,
and the fresh-process restart/SQLite restore rehearsal passes locally. Two
upstream dependency deprecation warnings remain. Six fixed sequential queries,
50 iterations each, measured local p95 query/serialization latency from 1.857 to
3.014 ms on this small index. This is not a production HTTP/load benchmark.

## Still required

- Successful remote container CI and a real hosted rollout with persistent-disk recovery verification.
- More useful source coverage, actual users and feedback.
- A representative human-reviewed evaluation dataset and final test evaluation.
- A fitted baseline and measured retrieval/ranking improvements only when reviewed data supports them.

See README.md for commands, docs/ARCHITECTURE.md for components and limitations,
docs/LABELING.md for the annotation protocol, and ROADMAP.md for later stages.
