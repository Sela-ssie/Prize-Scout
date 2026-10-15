#!/usr/bin/env python3
"""Terminal labelling tool. Walks data/corpus.jsonl, pre-fills the extractor's guess,
and records your accept-or-correct decision to data/labels.jsonl.

    python scripts/label.py                # label everything unlabelled
    python scripts/label.py --limit 50     # do a batch and stop
    python scripts/label.py --only-guessed-giveaway   # audit the extractor's positives

Resumable: every answer is appended immediately, and items already in labels.jsonl are
never shown again. Ctrl-C is safe.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.extractor import EXTRACTOR_VERSION, extract  # noqa: E402

BOLD, DIM, GREEN, RED, YELLOW, CYAN, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m",
)
NULL_TOKENS = {"-", "none", "null", "n/a"}


def load_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def parse_deadline(raw: str):
    """Accept YYYY-MM-DD or a full ISO timestamp; return a UTC ISO string."""
    raw = raw.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            dt = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


def prompt_text(field: str, guess, allow_null=True) -> object:
    shown = guess if guess else "(none)"
    hint = "enter=accept, '-'=none, or type a correction" if allow_null else "enter=accept"
    while True:
        print(f"  {CYAN}{field}{RESET} {DIM}[{hint}]{RESET}")
        print(f"    guess: {YELLOW}{shown}{RESET}")
        raw = input("    > ").strip()
        if raw == "":
            return guess
        if raw.lower() in NULL_TOKENS:
            return None
        if field == "deadline":
            parsed = parse_deadline(raw)
            if parsed is None:
                print(f"    {RED}not a date. use YYYY-MM-DD.{RESET}")
                continue
            return parsed
        return raw


def show(item, guess, index, total, remaining):
    description = item.get("description", "")
    print("\n" + "=" * 78)
    print(f"{BOLD}[{index}/{total}]{RESET} {DIM}{remaining} left{RESET}  "
          f"{BOLD}{(item.get('title') or '')[:70]}{RESET}")
    print(f"{DIM}{item['video_id']} | query={item.get('query', '?')} | "
          f"published={(item.get('published_at') or '')[:10]} | {len(description)} chars{RESET}")
    print("-" * 78)
    body = description if len(description) <= 1800 else description[:1800] + f"\n{DIM}... (truncated, 'f' to see all){RESET}"
    print(body if body.strip() else f"{DIM}(empty description){RESET}")
    print("-" * 78)
    verdict = f"{GREEN}GIVEAWAY{RESET}" if guess["is_giveaway"] else f"{RED}not a giveaway{RESET}"
    reason = f" ({guess['rejected_because']})" if guess.get("rejected_because") else ""
    print(f"{BOLD}extractor guess:{RESET} {verdict}{DIM}{reason}  conf={guess['confidence']}{RESET}")



SOURCE_FIELDS = ('video_id', 'channel_id', 'channel_title', 'title', 'url', 'published_at', 'description', 'fetched_at')


def _utcnow():
    return datetime.now(timezone.utc)


def _aware(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('packet clocks must be timezone-aware')
    return dt.astimezone(timezone.utc)


def load_packet(directory, *, now=None):
    """Validate all sources before showing any review or creating label output."""
    now = _utcnow() if now is None else now
    manifest = json.loads((directory / 'manifest.json').read_text())
    if any(key in manifest for key in ('eligible_bucket_counts', 'selected_balance', 'sampling_audit')):
        raise ValueError('packet exposes prediction audit counts; prepare a fresh blinded packet')
    if manifest.get('schema_version') != 1:
        raise ValueError('unsupported packet schema')
    annotation_clock = _aware(manifest['now'])
    if annotation_clock > now:
        raise ValueError('packet annotation clock is in the future')
    for name in ('corpus.jsonl', 'assignments.jsonl'):
        if not (directory / name).is_file():
            raise ValueError('incomplete packet: ' + name)
    corpus = load_jsonl(directory / 'corpus.jsonl')
    assignments = load_jsonl(directory / 'assignments.jsonl')
    by_id = {row['video_id']: row for row in assignments}
    if len(by_id) != len(assignments) or len({row['video_id'] for row in corpus}) != len(corpus):
        raise ValueError('duplicate packet video IDs')
    if set(by_id) != {row['video_id'] for row in corpus} or len(corpus) != manifest['selected_videos']:
        raise ValueError('packet assignment/source counts differ')
    groups = {}
    for row in corpus:
        assignment = by_id[row['video_id']]
        for key in ('group_id', 'suggested_split', 'source_snapshot_sha256'):
            if not row.get(key) or row[key] != assignment.get(key):
                raise ValueError('packet assignment metadata mismatch')
        group, split = row['group_id'], row['suggested_split']
        if split not in ('train', 'validation', 'test') or groups.setdefault(group, split) != split:
            raise ValueError('invalid or leaking packet split')
        position = int(hashlib.sha256(group.encode()).hexdigest()[:16], 16) % 100
        expected = 'train' if position < 60 else 'validation' if position < 80 else 'test'
        if split != expected:
            raise ValueError('packet split does not match group hash')
        source = {key: row[key] for key in SOURCE_FIELDS}
        digest = hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if digest != row['source_snapshot_sha256']:
            raise ValueError('packet source snapshot hash mismatch')
        fetched = _aware(row['fetched_at'])
        if fetched > annotation_clock or fetched > now or fetched < annotation_clock - timedelta(days=30) or fetched < now - timedelta(days=30):
            raise ValueError('packet source is stale or has an invalid fetch clock; refresh and prepare again')
    if len(groups) != manifest['selected_groups']:
        raise ValueError('packet group count differs')
    return corpus, annotation_clock.isoformat()


def _explicit(prompt, choices):
    while True:
        answer = input(prompt).strip().lower()
        if answer in choices:
            return answer
        print('Choose explicitly: ' + '/'.join(choices))


def _blind_text(field):
    while True:
        raw = input(field + " [blank or '-'=unknown/none] > ").strip()
        if not raw or raw.lower() in NULL_TOKENS:
            return None
        if field != 'deadline':
            return raw
        parsed = parse_deadline(raw)
        if parsed is not None:
            return parsed
        print('Use YYYY-MM-DD or an ISO timestamp.')


def run_blind(corpus, labels_path, *, reviewer=None, annotation_as_of=None, limit=None, packet=False):
    existing = load_jsonl(labels_path)
    by_id = {row['video_id']: row for row in corpus}
    if packet:
        for previous in existing:
            if previous['video_id'] in by_id:
                source = by_id[previous['video_id']]
                for field, expected in [('group_id', source['group_id']), ('split', source['suggested_split']),
                                        ('source_snapshot_sha256', source['source_snapshot_sha256']),
                                        ('annotation_as_of', annotation_as_of)]:
                    if previous.get(field) != expected:
                        raise ValueError('existing label provenance differs from packet: ' + field)
    done = {row['video_id'] for row in existing}
    queue = [row for row in corpus if row['video_id'] not in done]
    if limit is not None:
        queue = queue[:limit]
    print('Blind review; annotation clock: ' + annotation_as_of)
    print('is_giveaway means an eligible promotional offer, not explicitly closed/expired at this clock.')
    print('Record genuine closed promotions separately with promotion_exists and availability.')
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    with labels_path.open('a', encoding='utf-8') as stream:
        for item in queue:
            print('\n' + str(item.get('title') or '') + '\n' + str(item.get('description') or ''))
            print('video_id=' + item['video_id'] + ' | published=' + str(item.get('published_at') or ''))
            answer = _explicit('Eligible giveaway at annotation clock? [y/n/s/q] > ', ('y', 'n', 's', 'q'))
            if answer == 'q':
                break
            if answer == 's':
                continue
            eligible = answer == 'y'
            promotion = True if eligible else _explicit('Does a genuine promotion exist, even if closed? [y/n] > ', ('y', 'n')) == 'y'
            availability = 'not_applicable'
            if promotion:
                options = ('active', 'unknown') if eligible else ('active', 'expired', 'closed', 'unknown')
                availability = _explicit('Availability at annotation clock? [' + '/'.join(options) + '] > ', options)
            reviewed_at = _utcnow().isoformat()
            label = dict(video_id=item['video_id'], is_giveaway=eligible, promotion_exists=promotion,
                         availability=availability, prize=None, deadline=None, howto=None,
                         reviewer=reviewer, reviewed_at=reviewed_at, labelled_at=reviewed_at,
                         annotation_as_of=annotation_as_of, review_mode='blind', label_source='human')
            for field in ('group_id', 'source_snapshot_sha256'):
                if field in item:
                    label[field] = item[field]
            if 'suggested_split' in item:
                label['split'] = item['suggested_split']
            if promotion:
                label['prize'] = _blind_text('prize')
                label['deadline'] = _blind_text('deadline')
                label['howto'] = _blind_text('howto')
            stream.write(json.dumps(label, ensure_ascii=False) + '\n')
            stream.flush()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=ROOT / "data" / "corpus.jsonl")
    ap.add_argument("--labels", type=Path)
    ap.add_argument("--packet", type=Path, help="review a validated packet, always blind")
    ap.add_argument("--reviewer", help="human reviewer identity (required with --packet)")
    ap.add_argument("--blind", action="store_true", help="hide guesses for a legacy corpus")
    ap.add_argument("--limit", type=int, default=None, help="stop after this many items")
    ap.add_argument("--only-guessed-giveaway", action="store_true",
                    help="only show items the extractor calls a giveaway (audits precision)")
    args = ap.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        ap.error('--limit must be nonnegative')
    if (args.packet or args.blind) and args.only_guessed_giveaway:
        ap.error('--only-guessed-giveaway is incompatible with blind review')
    if args.packet and not (args.reviewer and args.reviewer.strip()):
        ap.error('--reviewer is required for packet review')
    args.labels = args.labels or ((args.packet / 'human_labels.jsonl') if args.packet else ROOT / 'data' / 'labels.jsonl')
    protected = [args.corpus] if not args.packet else [args.packet / name for name in ('manifest.json', 'corpus.jsonl', 'assignments.jsonl')]
    if args.labels.resolve() in {path.resolve() for path in protected}:
        ap.error('human labels must be separate from packet and corpus files')
    if args.packet or args.blind:
        try:
            corpus, clock = load_packet(args.packet) if args.packet else (load_jsonl(args.corpus), _utcnow().isoformat())
            return run_blind(corpus, args.labels, reviewer=args.reviewer, annotation_as_of=clock,
                             limit=args.limit, packet=bool(args.packet))
        except (ValueError, KeyError, TypeError, OSError) as error:
            print('Review refused: ' + str(error), file=sys.stderr)
            return 2

    corpus = load_jsonl(args.corpus)
    if not corpus:
        print(f"no corpus at {args.corpus}. Run scripts/collect_corpus.py first.", file=sys.stderr)
        return 2

    done = {row["video_id"] for row in load_jsonl(args.labels)}
    queue = [item for item in corpus if item["video_id"] not in done]

    guesses = {item["video_id"]: extract(item.get("description", ""),
                                         published_at=item.get("published_at"),
                                         title=item.get("title", ""))
               for item in queue}
    if args.only_guessed_giveaway:
        queue = [i for i in queue if guesses[i["video_id"]]["is_giveaway"]]
    if args.limit:
        queue = queue[: args.limit]

    print(f"{len(corpus)} in corpus, {len(done)} already labelled, {len(queue)} to go.")
    print(f"{DIM}keys: enter=accept guess  y/n=override is_giveaway  f=full text  "
          f"s=skip  q=save and quit{RESET}")

    args.labels.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with args.labels.open("a") as out:
        for index, item in enumerate(queue, 1):
            guess = guesses[item["video_id"]]
            while True:
                show(item, guess, index, len(queue), len(queue) - index + 1)
                choice = input(f"{BOLD}giveaway? [enter=accept / y / n / f / s / q]{RESET} > ").strip().lower()
                if choice == "f":
                    print(item.get("description", ""))
                    continue
                break

            if choice == "q":
                break
            if choice == "s":
                continue
            if choice == "y":
                is_giveaway = True
            elif choice == "n":
                is_giveaway = False
            else:
                is_giveaway = guess["is_giveaway"]

            label = {
                "video_id": item["video_id"],
                "is_giveaway": is_giveaway,
                "prize": None,
                "deadline": None,
                "howto": None,
                "labelled_at": datetime.now(timezone.utc).isoformat(),
                "guess_version": EXTRACTOR_VERSION,
                "guess_is_giveaway": guess["is_giveaway"],
                "guess_confidence": guess["confidence"],
            }
            if is_giveaway:
                label["prize"] = prompt_text("prize", guess["prize_summary"] if guess["is_giveaway"] else None)
                label["deadline"] = prompt_text("deadline", guess["deadline"] if guess["is_giveaway"] else None)
                label["howto"] = prompt_text("howto", guess["howto"] if guess["is_giveaway"] else None)

            out.write(json.dumps(label, ensure_ascii=False) + "\n")
            out.flush()
            written += 1
            mark = GREEN + "giveaway" + RESET if is_giveaway else RED + "not a giveaway" + RESET
            print(f"  {BOLD}saved{RESET} -> {mark}")

    print(f"\n{written} labels appended to {args.labels}. "
          f"Run scripts/eval.py to score the extractor against them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
