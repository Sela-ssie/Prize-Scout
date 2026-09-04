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
import json
import re
import sys
from datetime import datetime, timezone
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
          f"{BOLD}{item.get('title', '')[:70]}{RESET}")
    print(f"{DIM}{item['video_id']} | query={item.get('query', '?')} | "
          f"published={item.get('published_at', '')[:10]} | {len(description)} chars{RESET}")
    print("-" * 78)
    body = description if len(description) <= 1800 else description[:1800] + f"\n{DIM}... (truncated, 'f' to see all){RESET}"
    print(body if body.strip() else f"{DIM}(empty description){RESET}")
    print("-" * 78)
    verdict = f"{GREEN}GIVEAWAY{RESET}" if guess["is_giveaway"] else f"{RED}not a giveaway{RESET}"
    reason = f" ({guess['rejected_because']})" if guess.get("rejected_because") else ""
    print(f"{BOLD}extractor guess:{RESET} {verdict}{DIM}{reason}  conf={guess['confidence']}{RESET}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=ROOT / "data" / "corpus.jsonl")
    ap.add_argument("--labels", type=Path, default=ROOT / "data" / "labels.jsonl")
    ap.add_argument("--limit", type=int, default=None, help="stop after this many items")
    ap.add_argument("--only-guessed-giveaway", action="store_true",
                    help="only show items the extractor calls a giveaway (audits precision)")
    args = ap.parse_args()

    corpus = load_jsonl(args.corpus)
    if not corpus:
        print(f"no corpus at {args.corpus}. Run scripts/collect_corpus.py first.", file=sys.stderr)
        return 2

    done = {row["video_id"] for row in load_jsonl(args.labels)}
    queue = [item for item in corpus if item["video_id"] not in done]

    guesses = {item["video_id"]: extract(item.get("description", ""),
                                         published_at=item.get("published_at"))
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
