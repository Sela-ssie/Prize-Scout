#!/usr/bin/env python3
"""Discover sources and ingest uploads once, or repeat on a bounded interval."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import db
from src.ingestion import load_watchlist
from src.worker import ingestion_lock, pipeline_once


def positive(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watchlist', type=Path, default=ROOT / 'config/watchlist.json')
    parser.add_argument('--max-units', type=positive, default=200)
    parser.add_argument('--daily-max-units', type=positive, default=2000)
    parser.add_argument('--max-pages', type=positive, default=2, help='pages per channel per pass; resumes next pass')
    parser.add_argument('--interval', type=positive, help='repeat interval in seconds; omitted runs once')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    try:
        watchlist = load_watchlist(args.watchlist)
    except (OSError, ValueError) as exc:
        print(f'Unable to load watchlist: {exc}', file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps({'channels': len(watchlist), 'max_units_per_pass': args.max_units,
                          'daily_max_units': args.daily_max_units, 'max_pages_per_channel': args.max_pages,
                          'discovery_enabled': os.getenv('DISCOVERY_ENABLED', '1') == '1',
                          'channel_count_limit': None}))
        return 0
    if not os.getenv('YOUTUBE_API_KEY'):
        print('YOUTUBE_API_KEY is not configured', file=sys.stderr)
        return 2
    engine = db.create_db_and_tables(os.getenv('DATABASE_URL', 'sqlite:///./prizes.db'))

    async def run():
        while True:
            report = await pipeline_once(engine, watchlist, max_units=args.max_units,
                    daily_max_units=args.daily_max_units, max_pages=args.max_pages)
            print(json.dumps(report), flush=True)
            if args.interval is None:
                return 1 if report['errors'] else 0
            await asyncio.sleep(args.interval)
    try:
        with ingestion_lock(engine):
            return asyncio.run(run())
    except RuntimeError as exc:
        print(f'Unable to start ingestion: {exc}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
