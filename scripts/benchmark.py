#!/usr/bin/env python3
"""Measure local indexed-query latency against an existing SQLite index, read-only.

These fixed smoke queries are not a representative workload or an HTTP load test.
Stop ingestion while measuring. Output contains counts/timings, never source text,
database paths, credentials, or search results.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlmodel import create_engine
from src import db
from src.coverage import coverage_summary

CASES = (
    ('grouped_browse', dict(group_campaigns=True)),
    ('camera_search', dict(q='camera', group_campaigns=True)),
    ('books_category', dict(category='books', group_campaigns=True)),
    ('empty_search', dict(q='zzprizescoutnomatch', group_campaigns=True)),
    ('deadline_sort', dict(sort='deadline', group_campaigns=True)),
    ('individual_browse', dict(group_campaigns=False)),
)


def readonly_engine(url):
    parsed = make_url(url)
    if parsed.get_backend_name() != 'sqlite' or not parsed.database or parsed.database == ':memory:':
        raise ValueError('benchmark requires an existing file-backed SQLite index')
    source = Path(parsed.database).resolve()
    if not source.is_file():
        raise ValueError('index does not exist; benchmark never creates it')

    def connect():
        connection = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, check_same_thread=False, timeout=30)
        connection.execute('PRAGMA query_only=ON')
        return connection

    return create_engine('sqlite://', creator=connect)


def benchmark(engine, *, iterations=50, warmup=3):
    if not 1 <= iterations <= 2000 or not 0 <= warmup <= 20:
        raise ValueError('iterations must be 1–2000 and warmup 0–20')
    report = dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(),
        scope='local SQLite query + serialization; sequential fixed smoke queries; not HTTP or representative load',
        page_size=12, warmup_per_case=warmup, iterations_per_case=iterations,
        index=coverage_summary(engine)['totals'], cases=[])
    for name, parameters in CASES:
        for _ in range(warmup):
            db.list_index(engine, page_size=12, **parameters)
        timings, totals = [], set()
        for _ in range(iterations):
            started = time.perf_counter_ns()
            response = db.list_index(engine, page_size=12, **parameters)
            json.dumps(response, default=str)  # Include JSON serialization, no disk/network I/O.
            timings.append((time.perf_counter_ns() - started) / 1_000_000)
            totals.add(response['total'])
        ordered = sorted(timings)
        report['cases'].append(dict(name=name, result_totals=sorted(totals),
            latency_ms=dict(min=round(ordered[0], 3), p50=round(statistics.median(ordered), 3),
                p95=round(ordered[math.ceil(.95 * len(ordered)) - 1], 3), max=round(ordered[-1], 3))))
    report['counts_stable'] = all(len(case['result_totals']) == 1 for case in report['cases'])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations', type=int, default=50)
    parser.add_argument('--warmup', type=int, default=3)
    parser.add_argument('--out', type=Path, help='write a new report file; refuses overwrites')
    args = parser.parse_args(argv)
    engine = None
    try:
        engine = readonly_engine(os.getenv('DATABASE_URL', 'sqlite:///./prizes.db'))
        if not {'video', 'giveaway', 'ingestionstate', 'quotausage', 'video_fts'}.issubset(inspect(engine).get_table_names()):
            parser.error('index schema is incomplete; initialize it before benchmarking')
        report = benchmark(engine, iterations=args.iterations, warmup=args.warmup)
    except Exception:
        parser.error('unable to benchmark; check index schema and iteration limits (1–2000, warmup 0–20)')
    finally:
        if engine is not None:
            engine.dispose()
    output = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.out:
        try:
            with args.out.open('x', encoding='utf-8') as stream:
                stream.write(output)
        except OSError:
            parser.error('unable to write report; output must be a new writable file')
    else:
        print(output, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
