# Offline review workflow

Prepare a packet from the existing SQLite index without network calls:

```sh
.venv/bin/python scripts/prepare_review.py --limit 100 --seed review-1 \
  --now 2026-10-14T00:00:00Z --out-dir data/review-20261014
# Optional: --labels data/labels.jsonl skips the entire connected group of each reviewed ID.
```

`DATABASE_URL` selects the source database (default `sqlite:///./prizes.db`). Initialize/migrate it through the normal application before exporting. The exporter only reads it. Explicit output paths are supported; existing directories are refused. A new directory is exclusively reserved before writing; `manifest.json` is written last as its completion marker. Interrupted incomplete directories must be removed or replaced by choosing a new path.

The packet contains `manifest.json` with sampling metadata, `assignments.jsonl` with unlabelled IDs, suggested splits and snapshot hashes, and `corpus.jsonl` with source text and source timestamps. No human labels are invented. Source hashes include title, description, URL, channel, publication and fetch times; they identify exactly what a reviewer saw. The manifest has no source descriptions. Corpus exports are API cache too: keep them under ignored `data/`, and refresh/delete them on the source retention schedule. Exporting or re-extracting never renews `fetched_at`. Missing fetch dates, dates after the pinned clock, and data older than 30 days are excluded. If one member fails freshness, its entire group is excluded. Refreshing must be a genuine source refresh.

Selection rotates deterministically across channel and stored extractor positive, negative, rejection-reason and unextracted buckets. Groups have a deterministic anchor bucket; mixed groups can affect achieved balance. The manifest reports exclusions, including groups skipped when they cannot fit the remaining video limit. Prediction-bucket counts and achieved selection balance are available only in the queue builder's in-memory `sampling_audit` for the operator; they are never exported into the review packet. This enriched sample is **not representative** of live prevalence and does not support an unweighted population precision claim. Same source data, reviewed IDs, seed, limit and clock reproduce the same packet. Stored predictions are sampling metadata, never ground truth.

Campaign keys and supported entry links connect members. Normalized full title/description content and identical normalized nonempty descriptions also connect members, transitively. Every connected group stays together, including when an entire group must be skipped to respect `--limit`. IDs use the smallest campaign key in the component, or its smallest content hash. Suggested train/validation/test splits hash the group ID into 60/20/20 buckets and are independent of the sampling seed. These are approximate proportions, not a promise for small packets. Paraphrases, edited descriptions, unsupported entry links and later-discovered bridges may escape grouping. Inspect for additional duplicates before freezing a benchmark. New bridges can change group IDs; preserve the reviewed packet and reconcile assignments across packets before combining datasets.

Use training labels for fitting and validation labels for choices. Freeze the final test set before any model tuning. Test assignments and all source corpus rows omit model predictions. Prediction audit counts must stay outside reviewer materials: even aggregate counts can reveal a singleton held-out prediction by subtraction. The label tool refuses older packets that expose prediction counts in their manifest; prepare a fresh packet instead.

Review the packet with an identified human, without displaying or recomputing extractor predictions:

```sh
.venv/bin/python scripts/label.py --packet data/review-20261014 \
  --reviewer YOUR_NAME
```

Packet review is always blind for every split and writes separate `human_labels.jsonl` inside the packet by default. `--labels PATH` selects another human-label file. Each decision requires explicit `y` or `n`; Enter and invalid answers cannot accept a prediction. `s` skips and `q` exits. Decisions are appended and flushed immediately; restarting skips IDs already reviewed. Packet review refuses existing labels with conflicting group, split, source hash or annotation clock. `--limit` bounds the current review batch. `--only-guessed-giveaway` is rejected for blind review.

Before prompting, the tool verifies source snapshot hashes, one-to-one source/assignment consistency, group partitions, the timezone-aware pinned clock and source freshness at **both the annotation clock and the actual review time**. An old export cannot silently extend the API cache lifetime. Refresh stale source data and prepare a new packet; do not change timestamps to bypass expiry. The packet is checked for accidental corruption and inconsistent assignments, not cryptographically authenticated against a malicious rewrite of every file.

Human labels record `reviewer`, `reviewed_at`, `labelled_at`, `group_id`, `split`, `source_snapshot_sha256`, and `annotation_as_of`, plus the decisions themselves. No guesses or guess-version fields are written by blind review. Actual review time differs from the pinned annotation clock. Unknown prize/deadline/entry fields remain null. Existing legacy labels lacking these provenance fields need explicit provenance review before use in a frozen evaluation.

The benchmark's `is_giveaway` means **an eligible promotional offer that is not explicitly closed or expired at the pinned annotation clock**. Label a genuine expired/closed campaign `is_giveaway=false`, then answer that `promotion_exists=true` and set `availability=expired` or `closed`. This preserves the distinction between actual promotion existence and current eligibility. Eligible offers have `promotion_exists=true` and availability `active` or `unknown`; only select `unknown` when the offer is eligible but the available text does not establish timing. Nonpromotional source has `promotion_exists=false` and `availability=not_applicable`. An active promotion that fails giveaway eligibility can also be recorded with `is_giveaway=false`. Preserve ambiguity rather than inferring a deadline or promoting guesses into labels.

The existing legacy command `scripts/label.py --corpus PATH --labels PATH` keeps its guess-assisted workflow. Use `--blind` to hide guesses for legacy corpora too; legacy review does not have packet hash or freshness guarantees. Use `--packet` for frozen evaluation provenance and final test review. Real-source human review and adjudication are still required before claiming production precision, calibration quality, or trustworthiness.

Evaluate frozen human labels against the exact packet corpus:

```sh
.venv/bin/python scripts/eval.py --labels data/review-20261014/human_labels.jsonl \
  --corpus data/review-20261014/corpus.jsonl
# Final test is explicit, after choices are frozen:
.venv/bin/python scripts/eval.py --labels data/review-20261014/human_labels.jsonl \
  --corpus data/review-20261014/corpus.jsonl --split test
```

Assigned review labels default to **validation** evaluation. `--split train`, `--split test`, and `--split all` must be explicit; legacy fixtures without assignments retain their existing all-row behavior. Evaluation uses each human label's timezone-aware `annotation_as_of`; an explicit conflicting `--now` is refused. Provisional annotations and source snapshot/group/split mismatches are refused. Hash validation requires the original corpus fields; do not combine labels with refreshed text under the same video ID. Blind outputs identify `label_source=human`; this records the human review workflow, not independent verification of a reviewer's assertions.

Calibration reads the same validated dataset and honors its explicit frozen train/validation/test assignments exactly. Mixed assigned/unassigned rows or connected group/duplicate content crossing partitions are refused. Feature computation and runtime gate scoring use the row's own annotation clock. Legacy unassigned data retains deterministic grouped splitting and its explicit/fixture reference clock. Collect enough genuinely reviewed positives and negatives in every partition before attempting a fit; no fitted model or real-world quality claim is produced merely by preparing a packet.
