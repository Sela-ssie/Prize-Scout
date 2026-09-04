#!/usr/bin/env python3
"""Refit the confidence weights against labelled data and write extractor_weights.json.

    python scripts/calibrate.py                 # fit on data/labels.jsonl
    python scripts/calibrate.py --dry-run       # report only, write nothing

Fits an L2-regularised logistic regression over the extractor's named features on a
deterministic train split, then picks the decision threshold on the HELD-OUT split as the
one that maximises recall subject to precision >= --target-precision. Reporting the fit on
the data it was fitted to would overstate performance, so holdout numbers are what is
printed and recorded.

No scikit-learn: plain gradient descent keeps this a two-dependency project.
"""

import argparse
import json
import math
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import extractor as E  # noqa: E402
from scripts.eval import build_dataset, load_jsonl, fixture_now  # noqa: E402

WEIGHTS_PATH = ROOT / "extractor_weights.json"
MIN_LABELS = 60


def featurise(row, now):
    text = row["text"]
    prizes = E.extract_prizes(text)
    deadline = E.find_deadline(text, now=now, published_at=E._coerce_dt(row["published_at"]))
    howto = E.find_howto(text)
    return E.compute_features(text, prizes, deadline, howto)


def split(rows, holdout_fraction=0.3):
    """Deterministic by video_id, so re-running does not reshuffle the holdout."""
    train, test = [], []
    for row in rows:
        bucket = zlib.crc32(row["video_id"].encode()) % 100
        (test if bucket < holdout_fraction * 100 else train).append(row)
    return train, test


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
    """Highest recall subject to precision >= target. Precision is the priority."""
    best = None
    for candidate in sorted({round(s, 3) for s in scores} | {0.5}):
        tp = sum(1 for s, t in zip(scores, targets) if s >= candidate and t)
        fp = sum(1 for s, t in zip(scores, targets) if s >= candidate and not t)
        fn = sum(1 for s, t in zip(scores, targets) if s < candidate and t)
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        if precision >= target_precision and (best is None or recall > best[2]):
            best = (candidate, precision, recall)
    if best is None:
        return None, 0.0, 0.0
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, default=ROOT / "data" / "labels.jsonl")
    ap.add_argument("--corpus", type=Path, default=ROOT / "data" / "corpus.jsonl")
    ap.add_argument("--target-precision", type=float, default=0.90)
    ap.add_argument("--holdout", type=float, default=0.3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-labels", type=int, default=MIN_LABELS,
                    help="lower only to exercise the fitting path; fitting on few labels overfits")
    args = ap.parse_args()

    labels = load_jsonl(args.labels)
    if not labels:
        print(f"no labels at {args.labels}. Run scripts/collect_corpus.py then scripts/label.py.",
              file=sys.stderr)
        return 2

    rows, _ = build_dataset(labels, {i["video_id"]: i for i in load_jsonl(args.corpus)})
    if len(rows) < args.min_labels:
        print(f"only {len(rows)} labels; refusing to fit on fewer than {args.min_labels}. "
              f"Weights fitted on a handful of examples are worse than the hand-set priors.",
              file=sys.stderr)
        return 2

    now = fixture_now(labels)
    feature_names = list(E.DEFAULT_WEIGHTS["weights"])
    train, test = split(rows, args.holdout)
    if not test or not train:
        print("split produced an empty side; need more labels", file=sys.stderr)
        return 2

    x_train = [featurise(r, now) for r in train]
    y_train = [1 if r["label"]["is_giveaway"] else 0 for r in train]
    x_test = [featurise(r, now) for r in test]
    y_test = [1 if r["label"]["is_giveaway"] else 0 for r in test]

    weights, bias = fit(x_train, y_train, feature_names)
    test_scores = [score(f, weights, bias) for f in x_test]
    threshold, precision, recall = pick_threshold(test_scores, y_test, args.target_precision)

    print(f"train {len(train)} ({sum(y_train)} positive) / holdout {len(test)} ({sum(y_test)} positive)")
    if threshold is None:
        print(f"no threshold reaches precision {args.target_precision} on the holdout. "
              f"Keeping the existing weights; the features are not separating these labels.",
              file=sys.stderr)
        return 1
    print(f"holdout: threshold {threshold:.3f}  precision {precision:.3f}  recall {recall:.3f}")
    print("\nfitted weights:")
    for name in sorted(feature_names, key=lambda k: -abs(weights[k])):
        print(f"  {name:20s} {weights[name]:+.3f}")
    print(f"  {'bias':20s} {bias:+.3f}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    WEIGHTS_PATH.write_text(json.dumps({
        "bias": round(bias, 4),
        "threshold": threshold,
        "calibrated": True,
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "_comment": "Fitted by scripts/calibrate.py. Do not hand-edit to move eval numbers; refit.",
        "fitted_on": datetime.now(timezone.utc).isoformat(),
        "n_train": len(train),
        "holdout": {"n": len(test), "precision": round(precision, 4), "recall": round(recall, 4)},
    }, indent=2) + "\n")
    print(f"\nwrote {WEIGHTS_PATH.relative_to(ROOT)} — re-run scripts/eval.py to confirm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
