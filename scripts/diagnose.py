#!/usr/bin/env python3
"""Inspect the existing local index without ingestion, migrations, or API calls."""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from src.coverage import coverage_summary, diagnostic_report
from src.db import create_sync_engine


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--now', help='ISO 8601 clock for deterministic counts; naive values use UTC')
    parser.add_argument('--out', type=Path, help='write JSON to a new file (never overwrite)')
    parser.add_argument('--summary-only', action='store_true', help='emit only public-safe coverage metadata')
    args = parser.parse_args(argv)
    try:
        now = datetime.fromisoformat(args.now.replace('Z', '+00:00')) if args.now else None
    except ValueError:
        parser.error('--now must be an ISO 8601 datetime')
    url = os.getenv('DATABASE_URL', 'sqlite:///./prizes.db')
    try:
        parsed = make_url(url)
        if parsed.get_backend_name() == 'sqlite' and parsed.database not in (None, '', ':memory:'):
            if not Path(parsed.database).is_file():
                parser.error('database does not exist; initialize the index before diagnosis')
        engine = create_sync_engine(url)
        required = {'video', 'giveaway', 'ingestionstate', 'quotausage'}
        if not required.issubset(inspect(engine).get_table_names()):
            parser.error('database schema is incomplete; initialize the index before diagnosis')
        report = (coverage_summary if args.summary_only else diagnostic_report)(engine, now=now)
    except Exception:
        parser.error('unable to read index diagnostics; check the local database schema and configuration')
    finally:
        if 'engine' in locals():
            engine.dispose()
    output = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.out:
        try:
            with args.out.open('x', encoding='utf-8') as destination:
                destination.write(output)
        except FileExistsError:
            parser.error('--out already exists; choose a new file')
        except OSError:
            parser.error('unable to write --out file')
    else:
        print(output, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
