#!/usr/bin/env python3
"""Resolve curated YouTube channels into a quota-bounded ingestion watchlist."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import db
from src.ingestion import QuotaGuard, QuotaExceeded, load_watchlist
from src.youtube_client import YouTubeClient, YouTubeRequestError
from scripts.ingest import positive


def channel_input(value):
    if re.fullmatch(r'UC[A-Za-z0-9_-]{22}', value) or (
            value.startswith('@') and 1 < len(value) <= 101
            and not any(c.isspace() or c in '/?&#' for c in value)):
        return value
    raise argparse.ArgumentTypeError('must be a YouTube @handle or full UC channel ID')


def write_watchlist(path, rows):
    """Replace atomically only after every channel has resolved successfully."""
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as file:
            name = file.name
            json.dump({'channels': rows}, file, indent=2)
            file.write('\n')
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


async def configure(engine, channels, existing, *, max_units=20, daily_max_units=2000,
                    replace_existing=False, client=None):
    guard = QuotaGuard(engine, max_units, daily_max_units)
    client = client or YouTubeClient(guard=guard)
    client.guard = guard
    rows = {row['channel_id']: row for row in existing}
    for channel in dict.fromkeys(channels):
        # Existing canonical IDs do not require another network lookup.
        if channel in rows and not replace_existing:
            continue
        row = await client.resolve_channel(channel)
        if replace_existing or row['channel_id'] not in rows:
            rows[row['channel_id']] = row
    return list(rows.values()), guard.spent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--channel', action='append', required=True, type=channel_input,
                        help='@handle or UC channel ID; repeat for multiple channels')
    parser.add_argument('--out', type=Path, default=ROOT / 'config/watchlist.json')
    parser.add_argument('--max-units', type=positive, default=20)
    parser.add_argument('--daily-max-units', type=positive, default=2000)
    parser.add_argument('--replace-existing', action='store_true',
                        help='update resolved metadata for matching IDs; retain other entries')
    parser.add_argument('--dry-run', action='store_true', help='validate inputs without key, network, or writes')
    args = parser.parse_args()
    try:
        existing = load_watchlist(args.out) if args.out.exists() else []
        if args.dry_run:
            print(json.dumps({'channels': list(dict.fromkeys(args.channel)), 'existing_channels': len(existing),
                              'max_units': args.max_units, 'daily_max_units': args.daily_max_units,
                              'out': str(args.out)}))
            return 0
        if not os.getenv('YOUTUBE_API_KEY'):
            raise ValueError('YOUTUBE_API_KEY is not configured')
        engine = db.create_db_and_tables(os.getenv('DATABASE_URL', 'sqlite:///./prizes.db'))
        rows, spent = asyncio.run(configure(engine, args.channel, existing, max_units=args.max_units,
                        daily_max_units=args.daily_max_units, replace_existing=args.replace_existing))
        write_watchlist(args.out, rows)
        print(json.dumps({'channels': len(rows), 'quota_units': spent, 'out': str(args.out)}))
        return 0
    except (OSError, ValueError, YouTubeRequestError, QuotaExceeded) as exc:
        print(f'Unable to configure watchlist: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
