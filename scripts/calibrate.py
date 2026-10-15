#!/usr/bin/env python3
"""Refit the confidence weights against labelled data and write extractor_weights.json.

    python scripts/calibrate.py                 # fit on data/labels.jsonl
    python scripts/calibrate.py --dry-run       # report only, write nothing

Fits an L2-regularised logistic regression over the extractor's named features on a
deterministic grouped train split, picks the threshold on a separate validation split,
and evaluates the frozen model once on the test split. Exact duplicate source texts and
explicit label campaign_id/group_id values stay together. Related promotions with
changed text still require manually assigned campaign IDs before a release-quality fit.
Provisional agent labels are refused; adjudicate them first.

No scikit-learn: plain gradient descent keeps this a two-dependency project.
"""

import argparse
import json
import math
import sys
import zlib
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import extractor as E  # noqa: E402
from scripts.eval import build_dataset, load_jsonl, fixture_now, row_clock, aware_clock  # noqa: E402

WEIGHTS_PATH = ROOT / "extractor_weights.json"
MIN_LABELS = 60


def featurise(row, now):
    """Delegates to extractor.analyse so the fitted features cannot drift from the
    runtime ones. Fitting on features extract() never computes would silently produce
    weights for a model that does not exist."""
    return E.analyse(row["text"], title=row.get("title", ""),
                     published_at=row["published_at"], now=row_clock(row, now))["features"]


def split(rows, holdout_fraction=0.4):
    """Train/validation/test with duplicate and explicit campaign grouping.

    holdout_fraction is shared equally by validation and final test. Union-find keeps
    connected duplicate IDs, duplicate texts and campaign IDs in a single partition.
    """
    if not 0 < holdout_fraction < 1:
        raise ValueError("holdout must be between zero and one")
    assigned = [row['label'].get('split') for row in rows]
    frozen = any(value is not None for value in assigned)
    if frozen and any(value not in ('train', 'validation', 'test') for value in assigned):
        raise ValueError('refusing mixed assigned/unassigned or invalid calibration splits')
    parents = list(range(len(rows)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    seen = {}
    keys_by_row = []
    for i, row in enumerate(rows):
        content = re.sub(r"\s+", " ", row.get("text", "").strip().lower())
        if not content:
            content = row.get("title", "").strip().lower()
        keys = ["id:" + row["video_id"]]
        if content:
            keys.append("text:" + hashlib.sha256(content.encode()).hexdigest())
        for field in ('campaign_id', 'group_id'):
            campaign = row['label'].get(field)
            if campaign:
                keys.append('campaign:' + str(campaign))
        if frozen:
            # Match packet normalization, catching changed title/punctuation too.
            import unicodedata
            normalized = ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', row.get('text', '')).casefold()))
            if normalized:
                keys.append('normalized-text:' + hashlib.sha256(normalized.encode()).hexdigest())
        keys_by_row.append(keys)
        for key in keys:
            if key in seen:
                parents[root(i)] = root(seen[key])
            seen[key] = i
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(root(i), []).append((row, keys_by_row[i]))
    train, validation, test = [], [], []
    for group in groups.values():
        if frozen:
            group_splits = {row['label']['split'] for row, _ in group}
            if len(group_splits) != 1:
                raise ValueError('cross-group or duplicate split leakage in frozen assignments')
            destination = {'train': train, 'validation': validation, 'test': test}[group_splits.pop()]
            destination.extend(row for row, _ in group)
            continue
        # Prefer explicit campaign key, then source fingerprint, then video ID.
        keys = sorted({key for _, keys in group for key in keys})
        key = next((k for k in keys if k.startswith("campaign:")), None)
        key = key or next((k for k in keys if k.startswith("text:")), keys[0])
        bucket = zlib.crc32(key.encode()) / 2**32
        destination = test if bucket < holdout_fraction / 2 else (
            validation if bucket < holdout_fraction else train)
        destination.extend(row for row, _ in group)
    return train, validation, test


def runtime_scores(rows, weights, bias, now):
    """Use the deployment hard gates as well as its confidence model."""
    model = {"weights": weights, "bias": bias, "threshold": 0.0}
    scores = []
    for row in rows:
        result = E.extract(row["text"], title=row.get("title", ""),
                           published_at=row["published_at"], now=row_clock(row, now), weights=model)
        scores.append(result["confidence"] if result["is_giveaway"] else -1.0)
    return scores


def classification_metrics(scores, targets, threshold):
    tp = sum(s >= threshold and bool(t) for s, t in zip(scores, targets))
    fp = sum(s >= threshold and not t for s, t in zip(scores, targets))
    positives = sum(targets)
    return {"precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / positives if positives else 0.0, "tp": tp, "fp": fp}


def fit(x_rows, y, feature_names, epochs=4000, learning_rate=0.1, l2=0.01):
    weights = {name: 0.0 for name in feature_names}
    bias = 0.0
    n = len(y)
    for _ in range(epochs):
        grad_w = {name: 0.0 for name in feature_names}
        grad_b = 0.0
        for features, target in zip(x_rows, y):
            z = bias + sum(weights[k] * features.get(k, 0.0) for k in feature_names)
            prediction = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))
            error = prediction - target
            for name in feature_names:
                grad_w[name] += error * features.get(name, 0.0)
            grad_b += error
        for name in feature_names:
            weights[name] -= learning_rate * (grad_w[name] / n + l2 * weights[name])
        bias -= learning_rate * (grad_b / n)
    return weights, bias


def score(features, weights, bias):
    z = bias + sum(weights.get(k, 0.0) * v for k, v in features.items())
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))


def pick_threshold(scores, targets, target_precision):
    """Tune only on validation; rejecting everything is not a successful fit."""
    best = None
    for candidate in sorted({s for s in scores if s >= 0} | {0.5}):
        metrics = classification_metrics(scores, targets, candidate)
        if metrics["tp"] and metrics["precision"] >= target_precision:
            if best is None or metrics["recall"] > best[2]:
                best = candidate, metrics["precision"], metrics["recall"]
    return best if best is not None else (None, 0.0, 0.0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, default=ROOT / "data" / "labels.jsonl")
    ap.add_argument("--corpus", type=Path, default=ROOT / "data" / "corpus.jsonl")
    ap.add_argument("--target-precision", type=float, default=0.90)
    ap.add_argument("--holdout", type=float, default=0.4,
                    help="combined validation and test fraction; half for each")
    ap.add_argument("--now", help="required reference clock unless fixture provides now (ISO)")
    ap.add_argument("--target-recall", type=float, default=0.70)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-labels", type=int, default=MIN_LABELS,
                    help="lower only to exercise the fitting path; fitting on few labels overfits")
    args = ap.parse_args(argv)

    try:
        labels = load_jsonl(args.labels)
    except (ValueError, OSError) as error:
        print('Calibration refused: ' + str(error), file=sys.stderr)
        return 2
    if not labels:
        print(f"no labels at {args.labels}. Run scripts/collect_corpus.py then scripts/label.py.",
              file=sys.stderr)
        return 2

    if any(label.get("label_source") == "agent" or label.get("needs_review")
           for label in labels if label.get("video_id") != "_readme"):
        print("refusing provisional annotations; adjudicate and record reviewed provenance first", file=sys.stderr)
        return 2
    try:
        corpus = load_jsonl(args.corpus)
        if len({row['video_id'] for row in corpus}) != len(corpus):
            raise ValueError('duplicate source corpus video IDs')
        rows, missing = build_dataset(labels, {i['video_id']: i for i in corpus})
    except (ValueError, KeyError, TypeError, OSError) as error:
        print('Calibration refused: ' + str(error), file=sys.stderr)
        return 2
    if missing:
        print("labels missing source text; refusing incomplete fit", file=sys.stderr)
        return 2
    if len(rows) < args.min_labels:
        print(f"only {len(rows)} labels; refusing to fit on fewer than {args.min_labels}. "
              f"Weights fitted on a handful of examples are worse than the hand-set priors.",
              file=sys.stderr)
        return 2

    try:
        now = aware_clock(args.now) if args.now else (None if any(row['label'].get('annotation_as_of') for row in rows) else fixture_now(labels))
        if now is None and any(not row['label'].get('annotation_as_of') for row in rows):
            raise ValueError('supply --now to pin availability and relative-date features for legacy labels')
        for row in rows:
            row_clock(row, now)
    except (ValueError, KeyError, TypeError) as error:
        print('Calibration refused: ' + str(error), file=sys.stderr)
        return 2
    feature_names = list(E.DEFAULT_WEIGHTS["weights"])
    try:
        train, validation, test = split(rows, args.holdout)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    for name, partition in [("train", train), ("validation", validation), ("test", test)]:
        if {bool(r["label"]["is_giveaway"]) for r in partition} != {True, False}:
            print(f"{name} needs both positive and negative reviewed labels; collect more data", file=sys.stderr)
            return 2
    x_train = [featurise(r, now) for r in train]
    y_train = [int(r["label"]["is_giveaway"]) for r in train]
    y_validation = [int(r["label"]["is_giveaway"]) for r in validation]
    y_test = [int(r["label"]["is_giveaway"]) for r in test]
    weights, bias = fit(x_train, y_train, feature_names)
    validation_scores = runtime_scores(validation, weights, bias, now)
    threshold, precision, recall = pick_threshold(validation_scores, y_validation, args.target_precision)
    print(f"train {len(train)} / validation {len(validation)} / untouched test {len(test)}")
    if threshold is None or recall < args.target_recall:
        print("validation cannot meet quality floors; keeping existing weights", file=sys.stderr)
        return 1
    # No fitting or threshold adjustment follows this final-test evaluation.
    final = classification_metrics(runtime_scores(test, weights, bias, now), y_test, threshold)
    print(f"validation: precision {precision:.3f} recall {recall:.3f}")
    print(f"test: precision {final['precision']:.3f} recall {final['recall']:.3f}")
    if final["precision"] < args.target_precision or final["recall"] < args.target_recall:
        print("final test fails quality floors; keeping existing weights", file=sys.stderr)
        return 1
    print("\nfitted weights:")
    for name in sorted(feature_names, key=lambda k: -abs(weights[k])):
        print(f"  {name:20s} {weights[name]:+.3f}")
    print(f"  {'bias':20s} {bias:+.3f}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    WEIGHTS_PATH.write_text(json.dumps({
        "bias": bias,
        "threshold": threshold,
        "calibrated": True,
        "weights": weights,
        "_comment": "Fitted by scripts/calibrate.py. Do not hand-edit to move eval numbers; refit.",
        "fitted_on": datetime.now(timezone.utc).isoformat(),
        "n_train": len(train),
        "reference_now": now.isoformat() if now is not None else None,
        "annotation_clocks": sorted({r["label"]["annotation_as_of"] for r in rows if r["label"].get("annotation_as_of")}),
        "validation": {"n": len(validation), "precision": precision, "recall": recall},
        "test": {"n": len(test), **final},
    }, indent=2) + "\n")
    print(f"\nwrote {WEIGHTS_PATH.relative_to(ROOT)} — re-run scripts/eval.py to confirm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
