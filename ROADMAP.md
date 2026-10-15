# PrizeScout: flagship project vision

Build a trustworthy promotion-discovery platform with a useful search experience,
a replayable data pipeline, and measurable extraction/ranking quality. The same
product should support distinct interview stories in software engineering, data
engineering and applied ML. Each stage should ship before the next expands scope.

## 1. Ship and learn

The current YouTube MVP provides indexed search, general prize categories,
availability filters, sorting, source evidence, a safe demo, bounded background
ingestion, CI and a prepared persistent-disk deployment.

Finish a real hosted rollout, test restart/restore behavior, and recruit a small
set of users. Measure whether people find useful promotions and what prevents
them from trusting or using results. Record observed search latency, ingestion
lag, failures and hosting cost. An honest empty result is preferable to promoting
an old or unsupported offer.

## 2. Establish an evaluation asset

Foundation implemented: reproducible offline review packets with source hashes,
connected campaign/duplicate groups, frozen suggested partitions, blind human
labeling and pinned clocks. Evaluation defaults to validation, and calibration
preserves assigned splits. Human annotation, adjudication, representative sampling
and agreement measurement remain open; generated assignments are not labels.

Write one labeling rubric that separates promotion existence from current
availability, prize extraction, deadline evidence and entry instructions. Review
examples across categories, short/long descriptions, negatives, and campaigns.
Keep a grouped final test set untouched by tuning; publish dataset provenance,
sampling limits and annotation agreement without distributing retained API text
outside its allowed lifecycle.

Compare the current rules/score against a simple trained classifier and a
structured extraction model. Measure precision/recall and field accuracy per
category, calibration and abstention behavior, cost and latency. Preserve the
90% precision floor; include confidence intervals and recall costs rather than
claiming success from a handful of positive examples.

For ranking, build a fixed set of queries and reviewed relevance judgments.
Compare keyword/BM25, embedding retrieval and hybrid reranking using nDCG@10,
recall@10, latency and serving cost. Adopt the strongest measured tradeoff.
[BEIR](https://arxiv.org/abs/2104.08663) illustrates why diverse evaluation and a
strong lexical baseline matter; neural retrieval does not automatically win.

## 3. Make data quality and operations observable

First coverage slice implemented: public source inventory and freshness counts,
per-input positive/negative analysis provenance and local aggregate rejection,
input-coverage and quota diagnostics. Counts do not establish extraction quality.

Extend current raw-input retention and extractor versioning with run IDs,
per-stage counters, explicit schema versions and reproducible replay manifests.
Add a small operator dashboard showing source freshness, quota, failures,
category coverage, rejected-input reasons and extraction drift. Keep deleted and
expired API data out of the pipeline and its derived stores.

Exercise recovery from partial writes, API outages and changed upload cursors.
Benchmark ingestion throughput and user-query latency on a representative local
dataset. Move to a shared database/job queue when measured concurrency or volume
requires it; preserve idempotency and lifecycle rules during that migration.

## 4. Deepen the user experience

First slice implemented: shared-entry-link campaign grouping, exact field evidence,
source-by-source detail comparison, and a grouping toggle. Semantic matching,
reviewed grouping evaluation, and field adjudication remain future work.

Use feedback to prioritize saved searches, entry tracking and opt-in notifications.
Deduplicate repeated campaigns across creators and show source evidence for every
important field. Add additional platforms through a common adapter interface only
after the YouTube product demonstrates useful coverage and operation.

## Evidence for a resume and interviews

Maintain a concise architecture diagram, two-minute demo, design decisions and
failure/recovery examples. Report only measured numbers: fresh videos/channels,
reviewed test-set size and quality, p95 query latency, ingestion lag, quota per
run, hosting cost and actual user activity. Current synthetic tests and hand-set
weights do not establish a trained ML system or production accuracy.

The most persuasive story connects a user problem to a constraint, the design
chosen to address it, and evidence of the result. These are milestone goals,
not claims that every feature or benchmark already exists.
