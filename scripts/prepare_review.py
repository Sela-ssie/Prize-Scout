#!/usr/bin/env python3
"""Export a reproducible offline human-review packet; never writes labels or DB rows."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.db import create_sync_engine
from src.review import build_review_queue
from sqlalchemy import inspect
from sqlalchemy.engine import make_url


def export_review(queue, out_dir):
    """Reserve a new directory exclusively; refuse any existing output path.

    A failed write leaves an explicitly incomplete packet, never silently
    overwriting a previous review. Manifest is the completion marker written last.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=False)
    assignments = [{key: value for key, value in item.items() if key != 'source'} for item in queue['items']]
    corpus = [item['source'] | dict(group_id=item['group_id'], suggested_split=item['suggested_split'],
                                   source_snapshot_sha256=item['source_snapshot_sha256']) for item in queue['items']]
    for name, rows in [('corpus.jsonl', corpus), ('assignments.jsonl', assignments)]:
        with (out_dir / name).open('x', encoding='utf-8') as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    (out_dir / 'manifest.json').write_text(json.dumps(queue['manifest'], ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    return out_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--seed', default='prizescout-review-v1')
    parser.add_argument('--now', help='timezone-aware ISO clock for review and retention')
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--labels', type=Path, help='existing reviewed JSONL; skip entire groups containing these IDs')
    args = parser.parse_args(argv)
    try:
        now = datetime.fromisoformat(args.now.replace('Z', '+00:00')) if args.now else datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError()
    except ValueError:
        parser.error('--now must be a timezone-aware ISO datetime')
    reviewed = set()
    if args.labels:
        with args.labels.open(encoding='utf-8') as stream:
            reviewed = {json.loads(line)['video_id'] for line in stream if line.strip()}
    engine = None
    try:
        url = os.getenv('DATABASE_URL', 'sqlite:///./prizes.db')
        parsed = make_url(url)
        if parsed.get_backend_name() == 'sqlite' and parsed.database not in (None, '', ':memory:'):
            if not Path(parsed.database).is_file():
                parser.error('database does not exist; initialize the index before exporting')
        engine = create_sync_engine(url)
        if not {'video', 'giveaway'}.issubset(inspect(engine).get_table_names()):
            parser.error('database schema is incomplete; initialize the index before exporting')
        queue = build_review_queue(engine, limit=args.limit, seed=args.seed, now=now, reviewed_ids=reviewed)
    except Exception:
        parser.error('unable to read review inputs; check the local database schema and configuration')
    finally:
        if engine is not None:
            engine.dispose()
    out_dir = args.out_dir or ROOT / 'data' / ('review-' + now.strftime('%Y%m%dT%H%M%S%fZ'))
    export_review(queue, out_dir)
    print(json.dumps(dict(out_dir=str(out_dir), **queue['manifest'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
