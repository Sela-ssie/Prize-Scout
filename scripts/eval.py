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
import hashlib
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

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


SOURCE_FIELDS = ('video_id', 'channel_id', 'channel_title', 'title', 'url', 'published_at', 'description', 'fetched_at')


def aware_clock(value):
    try:
        clock = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
        if clock.tzinfo is None or clock.utcoffset() is None:
            raise ValueError('clock must be timezone-aware')
        return clock.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError('annotation clock must be a timezone-aware ISO datetime') from error


def reject_provisional(label):
    if label.get('needs_review') or label.get('provisional') or label.get('label_source') in ('agent', 'model', 'provisional', 'automatic'):
        raise ValueError('refusing provisional annotations; human adjudication is required')


def row_clock(row, now=None):
    """A reviewed annotation owns its clock; a caller cannot silently change it."""
    reject_provisional(row['label'])
    pinned = row['label'].get('annotation_as_of')
    if pinned is None:
        return now
    clock = aware_clock(pinned)
    if now is not None and aware_clock(now) != clock:
        raise ValueError('explicit --now conflicts with label annotation_as_of for ' + row['video_id'])
    return clock


def evaluation_rows(rows, split=None):
    assigned = [row['label'].get('split') for row in rows]
    if any(value is not None for value in assigned):
        if any(value not in ('train', 'validation', 'test') for value in assigned):
            raise ValueError('refusing mixed assigned/unassigned or invalid evaluation splits')
        selected = split or 'validation'
    else:
        selected = split or 'all'
        if selected != 'all':
            raise ValueError('legacy labels have no explicit split; use --split all')
    if selected not in ('train', 'validation', 'test', 'all'):
        raise ValueError('unknown evaluation split')
    return rows if selected == 'all' else [row for row in rows if row['label']['split'] == selected]


def build_dataset(labels, corpus_by_id):
    """Join labels to their text. Seed fixtures carry their own text inline."""
    rows, missing = [], 0
    seen = set()
    for label in labels:
        if label.get('video_id') == '_readme':
            continue
        reject_provisional(label)
        if not isinstance(label.get('is_giveaway'), bool):
            raise ValueError('labels require an explicit human boolean is_giveaway decision')
        if label.get('annotation_as_of') is not None:
            aware_clock(label['annotation_as_of'])
        item = corpus_by_id.get(label['video_id'])
        provenance = 'source_snapshot_sha256' in label or 'split' in label
        if provenance:
            if label['video_id'] in seen:
                raise ValueError('duplicate provenance label video ID: ' + label['video_id'])
            if item is None:
                raise ValueError('provenance label has no source corpus row: ' + label['video_id'])
            if label.get('label_source') != 'human' or not label.get('reviewer') or not label.get('reviewed_at'):
                raise ValueError('provenance labels require identified human review')
            aware_clock(label['reviewed_at'])
            aware_clock(label.get('annotation_as_of'))
            if label.get('split') not in ('train', 'validation', 'test') or not label.get('group_id'):
                raise ValueError('provenance labels require a group and explicit split')
            try:
                source = {field: item[field] for field in SOURCE_FIELDS}
            except KeyError as error:
                raise ValueError('source corpus lacks snapshot fields') from error
            digest = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if digest != label.get('source_snapshot_sha256') or digest != item.get('source_snapshot_sha256'):
                raise ValueError('source snapshot hash mismatch for ' + label['video_id'])
            if label['group_id'] != item.get('group_id') or label['split'] != item.get('suggested_split'):
                raise ValueError('label/source group or split mismatch for ' + label['video_id'])
        seen.add(label['video_id'])
        text = None if provenance else label.get('description')
        published_at = label.get('published_at')
        title = label.get('title', '')
        if text is None:
            if item is None:
                missing += 1
                continue
            text = item.get('description', '')
            published_at = item.get('published_at')
            title = item.get('title', '')
        rows.append(dict(video_id=label['video_id'], title=title or '', text=text,
                         published_at=published_at, label=label, source=label.get('source', 'labelled')))

    return rows, missing


def evaluate(rows, now=None, split=None):
    # Validate all supplied clocks, including rows outside the requested split.
    for row in rows:
        row_clock(row, now)
    rows = evaluation_rows(rows, split)
    tp = fp = fn = tn = 0
    false_positives, false_negatives = [], []
    field_stats = {f: {"correct": 0, "total": 0} for f in ("prize", "deadline", "howto")}
    detected_stats = {f: {"correct": 0, "total": 0} for f in ("prize", "deadline", "howto")}

    for row in rows:
        result = extract(row["text"], published_at=row["published_at"], now=row_clock(row, now),
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", type=Path, default=None)
    ap.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    ap.add_argument("--show-errors", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--now", default=None,
                    help="pin the clock (ISO). Defaults to the fixture's own 'now', else real time.")
    ap.add_argument('--split', choices=('train', 'validation', 'test', 'all'), default=None,
                    help='defaults to validation for assigned review labels, all for legacy fixtures; test must be explicit')
    args = ap.parse_args(argv)

    labels_path, source_note = resolve_labels(args.labels)
    try:
        labels = load_jsonl(labels_path)
    except (ValueError, OSError) as error:
        print('Evaluation refused: ' + str(error), file=sys.stderr)
        return 2
    if not labels:
        print(f"no labels at {labels_path}", file=sys.stderr)
        return 2

    try:
        corpus = load_jsonl(args.corpus)
        corpus_by_id = {item['video_id']: item for item in corpus}
        if len(corpus_by_id) != len(corpus):
            raise ValueError('duplicate source corpus video IDs')
        rows, missing = build_dataset(labels, corpus_by_id)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print('Evaluation refused: ' + str(error), file=sys.stderr)
        return 2
    if missing:
        print(f"{YELLOW}warning: {missing} labels have no matching corpus item{RESET}", file=sys.stderr)
    if not rows:
        print("nothing to evaluate", file=sys.stderr)
        return 2

    try:
        # Fixture clocks only apply to legacy rows. Packet labels own their clocks.
        now = aware_clock(args.now) if args.now else (None if any(r['label'].get('annotation_as_of') for r in rows) else fixture_now(labels))
        selected = evaluation_rows(rows, args.split)
        if not selected:
            raise ValueError('no labels in requested evaluation split')
        metrics = evaluate(rows, now=now, split=args.split)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print('Evaluation refused: ' + str(error), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({k: v for k, v in metrics.items() if not k.startswith("_")}, indent=2))
    else:
        report(metrics, args.show_errors, source_note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
