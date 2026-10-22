#!/usr/bin/env python3
"""Discover recent YouTube candidates and new sources within explicit API budgets."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import db
from src.discovery import discover_once, load_queries
from src.worker import ingestion_lock
from src.youtube_client import YouTubeRequestError
from scripts.ingest import positive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queries', type=Path, default=ROOT/'config/discovery.json')
    parser.add_argument('--max-units', type=positive, default=64, help='total API requests per pass, including retries')
    parser.add_argument('--daily-max-units', type=positive, default=2000, help='shared non-search daily allowance')
    parser.add_argument('--max-search-calls', type=positive, default=16)
    parser.add_argument('--daily-search-calls', type=positive, default=80, help='shared Search Queries daily allowance')
    parser.add_argument('--lookback-days', type=positive, default=14)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    engine = None
    try:
        queries = load_queries(args.queries)
        if args.lookback_days > 365:
            raise ValueError('lookback-days must be at most 365')
        options = dict(max_units=args.max_units, daily_max_units=args.daily_max_units,
            max_search_calls=args.max_search_calls, daily_max_search_calls=args.daily_search_calls,
            lookback_days=args.lookback_days)
        if args.dry_run:
            print(json.dumps(dict(queries=len(queries), channel_count_limit=None, **options)))
            return 0
        if not os.getenv('YOUTUBE_API_KEY'):
            raise ValueError('YOUTUBE_API_KEY is not configured')
        engine = db.create_db_and_tables(os.getenv('DATABASE_URL', 'sqlite:///./prizes.db'))
        with ingestion_lock(engine):
            report = asyncio.run(discover_once(engine, queries, **options))
        print(json.dumps(report))
        return 1 if report['errors'] else 0
    except (OSError, ValueError, RuntimeError, YouTubeRequestError) as exc:
        print(f'Discovery could not complete: {exc}', file=sys.stderr)
        return 2
    finally:
        if engine:
            engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
