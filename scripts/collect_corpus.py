#!/usr/bin/env python3
"""Collect real YouTube descriptions into data/corpus.jsonl, once, under a quota cap.

Run this ONE time. After it succeeds, every extractor change can be evaluated offline
for free. Never tune the extractor by re-running live searches.

    python scripts/collect_corpus.py --max-units 3000

Quota costs (YouTube Data API v3, 10,000 units/day for the whole application):
    search.list      100 units   <- the expensive one
    videos.list        1 unit    per call, up to 50 ids per call
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

SEARCH_COST = 100
VIDEOS_COST = 1
BASE = "https://www.googleapis.com/youtube/v3"

# Deliberately varied: obvious giveaways, ambiguous phrasing, and queries that will
# return non-giveaways. A corpus of only true positives cannot measure precision.
DEFAULT_QUERIES = [
    "giveaway",
    "enter to win",
    "free giveaway 2026",
    "iphone giveaway",
    "gaming pc giveaway",
    "gift card giveaway",
    "sweepstakes contest",
    "win a car",
    "subscriber giveaway winner announcement",
    "raffle prize draw",
    "unboxing review",          # near-miss: mentions prizes, not a giveaway
    "product review giveaway mention",
    "how to win at chess",      # 'win' as a verb, must not be a giveaway
    "winter vlog",              # substring trap for the old 'win' bug
    "twin turbo build",         # substring trap
    "official music video",     # must be rejected
]


class QuotaGuard:
    """Refuses to spend past the cap. Checked *before* each call, never after."""

    def __init__(self, max_units: int):
        self.max_units = max_units
        self.spent = 0

    def can_afford(self, cost: int) -> bool:
        return self.spent + cost <= self.max_units

    def charge(self, cost: int) -> None:
        if not self.can_afford(cost):
            raise RuntimeError(
                f"quota guard: refusing to spend {cost} units, "
                f"{self.spent}/{self.max_units} already used"
            )
        self.spent += cost


def search_page(client, key, query, page_token, guard, published_after):
    guard.charge(SEARCH_COST)
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": "50",
        "order": "date",
        "publishedAfter": published_after,
        "key": key,
    }
    if page_token:
        params["pageToken"] = page_token
    r = client.get(f"{BASE}/search", params=params, timeout=30.0)
    r.raise_for_status()
    return r.json()


def fetch_descriptions(client, key, ids, guard):
    out = {}
    for i in range(0, len(ids), 50):
        batch = ids[i: i + 50]
        if not guard.can_afford(VIDEOS_COST):
            print(f"  quota guard: stopping before videos.list ({guard.spent}/{guard.max_units})")
            break
        guard.charge(VIDEOS_COST)
        r = client.get(
            f"{BASE}/videos",
            params={"part": "snippet", "id": ",".join(batch), "key": key},
            timeout=30.0,
        )
        r.raise_for_status()
        for item in r.json().get("items", []):
            snippet = item["snippet"]
            out[item["id"]] = {
                "video_id": item["id"],
                "channel_id": snippet.get("channelId"),
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "published_at": snippet.get("publishedAt", ""),
            }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-units", type=int, default=3000,
                    help="hard quota cap; the script exits rather than exceed it (default 3000)")
    ap.add_argument("--target", type=int, default=400, help="stop once this many items are collected")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "corpus.jsonl")
    ap.add_argument("--published-after", default="2025-01-01T00:00:00Z")
    ap.add_argument("--queries", nargs="*", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print the plan and spend nothing")
    args = ap.parse_args()

    queries = args.queries or DEFAULT_QUERIES
    guard = QuotaGuard(args.max_units)

    if args.dry_run:
        per_query = SEARCH_COST + 1
        print(f"queries: {len(queries)}, ~{per_query} units each, "
              f"~{len(queries) * per_query} units total, cap {args.max_units}")
        return 0

    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("YOUTUBE_API_KEY is not set. Copy .env.example to .env and fill it in.", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                existing[row["video_id"]] = row
        print(f"resuming: {len(existing)} items already in {args.out}")

    collected = dict(existing)
    fetched_at = datetime.now(timezone.utc).isoformat()

    with httpx.Client() as client:
        for query in queries:
            if len(collected) >= args.target:
                print("target reached")
                break
            if not guard.can_afford(SEARCH_COST + VIDEOS_COST):
                print(f"quota guard: cannot afford another query "
                      f"({guard.spent}/{guard.max_units}); stopping")
                break
            try:
                data = search_page(client, key, query, None, guard, args.published_after)
            except RuntimeError as exc:
                print(f"  {exc}")
                break
            except httpx.HTTPStatusError as exc:
                print(f"  {query!r}: HTTP {exc.response.status_code} {exc.response.text[:200]}")
                continue

            ids = [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]
            new_ids = [v for v in ids if v not in collected]
            details = fetch_descriptions(client, key, new_ids, guard)
            for vid, row in details.items():
                row["query"] = query
                row["fetched_at"] = fetched_at
                collected[vid] = row
            print(f"  {query!r}: +{len(details)} new (total {len(collected)}, "
                  f"{guard.spent}/{guard.max_units} units)")
            time.sleep(0.2)

    with args.out.open("w") as f:
        for row in collected.values():
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    with_text = sum(1 for r in collected.values() if r.get("description", "").strip())
    print(f"\nwrote {len(collected)} items to {args.out} ({with_text} with a non-empty description)")
    print(f"QUOTA UNITS SPENT: {guard.spent} (cap {args.max_units})")
    if len(collected) < 300:
        print(f"NOTE: {len(collected)} items is below the 300-500 target; "
              f"re-run with a higher --max-units to add more.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
