#!/usr/bin/env python3
"""Score the extractor against labelled data.

    python scripts/eval.py                       # uses data/labels.jsonl if present,
                                                 # otherwise the seed fixture set
    python scripts/eval.py --json               # machine-readable, for the regression test
    python scripts/eval.py --show-errors 15     # more failure detail

Priority is PRECISION on is_giveaway: a false positive costs far more user trust than a
miss. Recall is reported, never optimised at precision's expense.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.extractor import EXTRACTOR_VERSION, extract  # noqa: E402

DEFAULT_LABELS = ROOT / "data" / "labels.jsonl"
DEFAULT_CORPUS = ROOT / "data" / "corpus.jsonl"
SEED_LABELS = ROOT / "tests" / "fixtures" / "seed_labels.jsonl"

BOLD, DIM, GREEN, RED, YELLOW, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_TOKENS = re.compile(r"[a-z0-9$]+")


def load_jsonl(path: Path):
    if not path or not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def text_match(predicted, actual, threshold: float = 0.5) -> bool:
    """Token-F1 overlap. Free text will not match exactly, and demanding that it does
    would make per-field accuracy meaningless."""
    if not predicted and not actual:
        return True
    if not predicted or not actual:
        return False
    a = set(_TOKENS.findall(str(predicted).lower()))
    b = set(_TOKENS.findall(str(actual).lower()))
    if not a or not b:
        return False
    overlap = len(a & b)
    if not overlap:
        return False
    precision, recall = overlap / len(a), overlap / len(b)
    return 2 * precision * recall / (precision + recall) >= threshold


def date_match(predicted, actual) -> bool:
    if not predicted and not actual:
        return True
    if not predicted or not actual:
        return False
    return str(predicted)[:10] == str(actual)[:10]


def fixture_now(labels):
    """Fixture sets pin their own clock so hardcoded dates do not rot."""
    for label in labels:
        if label.get("video_id") == "_readme" and label.get("now"):
            from datetime import datetime
            return datetime.fromisoformat(label["now"].replace("Z", "+00:00"))
    return None


def build_dataset(labels, corpus_by_id):
    """Join labels to their text. Seed fixtures carry their own text inline."""
    rows, missing = [], 0
    for label in labels:
        if label.get("video_id") == "_readme":
            continue
        text = label.get("description")
        published_at = label.get("published_at")
        title = label.get("title", "")
        if text is None:
            item = corpus_by_id.get(label["video_id"])
            if item is None:
                missing += 1
                continue
            text = item.get("description", "")
            published_at = item.get("published_at")
            title = item.get("title", "")
        rows.append({
            "video_id": label["video_id"],
            "title": title,
            "text": text,
            "published_at": published_at,
            "label": label,
            "source": label.get("source", "labelled"),
        })
    return rows, missing


def evaluate(rows, now=None):
    tp = fp = fn = tn = 0
    false_positives, false_negatives = [], []
    field_stats = {f: {"correct": 0, "total": 0} for f in ("prize", "deadline", "howto")}
    detected_stats = {f: {"correct": 0, "total": 0} for f in ("prize", "deadline", "howto")}

    for row in rows:
        result = extract(row["text"], published_at=row["published_at"], now=now,
                         title=row.get("title", ""))
        predicted = result["is_giveaway"]
        actual = bool(row["label"]["is_giveaway"])

        if predicted and actual:
            tp += 1
        elif predicted and not actual:
            fp += 1
            false_positives.append((result["confidence"], row, result))
        elif not predicted and actual:
            fn += 1
            false_negatives.append((result["confidence"], row, result))
        else:
            tn += 1

        if actual:
            checks = {
                "prize": text_match(result["prize_summary"], row["label"].get("prize")),
                "deadline": date_match(result["deadline"], row["label"].get("deadline")),
                "howto": text_match(result["howto"], row["label"].get("howto")),
            }
            for field, ok in checks.items():
                field_stats[field]["total"] += 1
                field_stats[field]["correct"] += int(ok)
                if predicted:
                    detected_stats[field]["total"] += 1
                    detected_stats[field]["correct"] += int(ok)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "extractor_version": EXTRACTOR_VERSION,
        "n": len(rows),
        "n_positive": tp + fn,
        "n_negative": fp + tn,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "fields": {
            f: {
                "accuracy_over_true_giveaways": round(s["correct"] / s["total"], 4) if s["total"] else None,
                "accuracy_over_detected": round(
                    detected_stats[f]["correct"] / detected_stats[f]["total"], 4
                ) if detected_stats[f]["total"] else None,
                "n": s["total"],
            }
            for f, s in field_stats.items()
        },
        "_false_positives": sorted(false_positives, key=lambda x: -x[0]),
        "_false_negatives": sorted(false_negatives, key=lambda x: -x[0]),
    }


def snippet(text: str, limit: int = 220) -> str:
    flat = re.sub(r"\s+", " ", (text or "")).strip()
    return flat[:limit] + ("..." if len(flat) > limit else "")


def report(metrics, show_errors: int, source_note: str):
    print(f"\n{BOLD}extractor {metrics['extractor_version']}{RESET}  {DIM}{source_note}{RESET}")
    print(f"{metrics['n']} items: {metrics['n_positive']} giveaways, {metrics['n_negative']} not\n")

    colour = GREEN if metrics["precision"] >= 0.90 else RED
    print(f"{BOLD}is_giveaway{RESET}")
    print(f"  precision {colour}{metrics['precision']:.3f}{RESET}   "
          f"recall {metrics['recall']:.3f}   f1 {metrics['f1']:.3f}")
    print(f"  {DIM}TP {metrics['tp']}  FP {metrics['fp']}  FN {metrics['fn']}  TN {metrics['tn']}{RESET}")
    if metrics["precision"] < 0.90:
        print(f"  {RED}below the 0.90 precision target{RESET}")

    print(f"\n{BOLD}per-field accuracy{RESET} {DIM}(over labelled giveaways){RESET}")
    for field, stats in metrics["fields"].items():
        over_all = stats["accuracy_over_true_giveaways"]
        over_detected = stats["accuracy_over_detected"]
        fmt = lambda v: f"{v:.3f}" if v is not None else "  n/a"
        print(f"  {field:9s} {fmt(over_all)}  {DIM}(of the {stats['n']} true giveaways){RESET}"
              f"   {fmt(over_detected)} {DIM}(of those we detected){RESET}")

    if metrics["_false_positives"]:
        print(f"\n{BOLD}{RED}worst false positives{RESET} "
              f"{DIM}(highest confidence first — these cost the most trust){RESET}")
        for confidence, row, result in metrics["_false_positives"][:show_errors]:
            print(f"  {RED}conf {confidence:.3f}{RESET} {row['video_id']} "
                  f"{DIM}{row['title'][:50]}{RESET}")
            print(f"    prize={result['prize_summary']!r} deadline={result['deadline']}")
            print(f"    {DIM}{snippet(row['text'])}{RESET}")
    else:
        print(f"\n{GREEN}no false positives{RESET}")

    if metrics["_false_negatives"]:
        print(f"\n{BOLD}{YELLOW}missed giveaways{RESET} {DIM}(highest confidence first){RESET}")
        for confidence, row, result in metrics["_false_negatives"][:show_errors]:
            print(f"  {YELLOW}conf {confidence:.3f}{RESET} {row['video_id']} "
                  f"{DIM}rejected: {result['rejected_because']}{RESET}")
            print(f"    {DIM}{snippet(row['text'], 160)}{RESET}")
    print()


def resolve_labels(explicit: Path):
    if explicit:
        return explicit, f"labels: {explicit}"
    if DEFAULT_LABELS.exists():
        return DEFAULT_LABELS, f"labels: {DEFAULT_LABELS.relative_to(ROOT)}"
    return SEED_LABELS, (f"labels: {SEED_LABELS.relative_to(ROOT)} "
                         f"(SEED FIXTURES — not a substitute for a real labelled corpus; "
                         f"run collect_corpus.py then label.py)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, default=None)
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--show-errors", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--now", default=None,
                    help="pin the clock (ISO). Defaults to the fixture's own 'now', else real time.")
    args = ap.parse_args()

    labels_path, source_note = resolve_labels(args.labels)
    labels = load_jsonl(labels_path)
    if not labels:
        print(f"no labels at {labels_path}", file=sys.stderr)
        return 2

    corpus_by_id = {item["video_id"]: item for item in load_jsonl(args.corpus)}
    rows, missing = build_dataset(labels, corpus_by_id)
    if missing:
        print(f"{YELLOW}warning: {missing} labels have no matching corpus item{RESET}", file=sys.stderr)
    if not rows:
        print("nothing to evaluate", file=sys.stderr)
        return 2

    if args.now:
        from datetime import datetime
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
    else:
        now = fixture_now(labels)
    metrics = evaluate(rows, now=now)
    if args.json:
        print(json.dumps({k: v for k, v in metrics.items() if not k.startswith("_")}, indent=2))
    else:
        report(metrics, args.show_errors, source_note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
