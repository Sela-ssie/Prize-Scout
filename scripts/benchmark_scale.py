#!/usr/bin/env python3
"""Disposable fictional SQLite growth check; no API calls or production DB access.

All rows pass through the actual bounded source writer, including promotion,
evidence, and FTS maintenance. This is a reproducible local smoke workload,
not a capacity guarantee or a representative YouTube extraction benchmark.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import statistics
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from src import db

FIXTURE_NOW = datetime.now(timezone.utc)
FIXTURE_DEADLINE = (FIXTURE_NOW + timedelta(days=14)).replace(hour=23, minute=59, second=59, microsecond=0)


def fictional_row(i, positives=1000):
    positive = i < positives
    # Enough retained prose to exercise the FTS and source store meaningfully.
    description = (f'Fictional channel workshop episode {i}. We compare camera lighting, '
        'tripod placement and sound treatment in a small studio. The presenter explains '
        'each setup and shares practical notes on recording a clear tutorial. '
        'This generated fixture describes no real creator, offer, product or promotion.\n')
    extracted = None
    if positive:
        # Shared links exercise campaign grouping; every tenth campaign also has
        # a closed source, exercising correlated current-only suppression.
        description += f'Fictional camera giveaway.\nEnter at https://gleam.io/{i // 2:05x}/fictional\n'
        description += 'Subscribe and comment to enter.\nGiveaway ends ' + FIXTURE_DEADLINE.strftime('%B %d, %Y') + '.\n'
        if i % 20 == 1:
            description += 'This giveaway is closed; winner announced.\n'
        if i % 5 == 0:
            description += 'Use code FICTIONAL for 10% off at https://example.com/fictional\n'
        extracted = dict(prizes='camera', prizes_structured=[dict(item='camera')],
            deadline=FIXTURE_DEADLINE.isoformat(), howto='Subscribe and comment to enter.',
            confidence=.9 - (i % 10) / 100, extractor_version='fictional-scale-v1')
    return dict(video=dict(id=f'fictional-{i}', channel_id=f'fictional-channel-{i % 5000}',
        channel_title='Fictional channel', title=f'Fictional camera workshop {i}',
        description=description, published_at=FIXTURE_NOW.isoformat()),
        extracted=extracted, analysis=dict(is_giveaway=positive, confidence=.9 if positive else .1,
        extractor_version='fictional-scale-v1', rejected_because=None if positive else 'fixture_negative'))


def counts(engine):
    with engine.connect() as connection:
        return {table: connection.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar_one()
                for table in ('video', 'giveaway', 'promotionmention')}


def check_integrity(engine, path):
    """Check persisted state independently of long-lived pooled FTS handles.

    On SQLite 3.50.4 the 100k fixture reproduced an initial pooled xIntegrity
    warning after another connection committed deletions. A fresh connection
    passed before any FTS command, while explicit checks then cleared the pooled
    warning without repair. Keep that observation visible; never rebuild here.
    """
    with engine.connect() as connection:
        pooled = [row[0] for row in connection.execute(text('PRAGMA integrity_check')).all()]
    result = dict(sqlite_version=sqlite3.sqlite_version, pooled_connection_check=pooled)
    # Open independently BEFORE issuing any FTS special command, so a false
    # success cannot be explained by a preceding implicit repair or rebuild.
    with closing(sqlite3.connect(path)) as fresh:
        result['sqlite'] = [row[0] for row in fresh.execute('PRAGMA integrity_check').fetchall()]
        try:
            fresh.execute("INSERT INTO video_fts(video_fts, rank) VALUES('integrity-check', 1)")
            result['fts'] = 'ok'
        except sqlite3.DatabaseError as exc:
            result['fts'] = type(exc).__name__
        result['sqlite_after_fts_check'] = [row[0] for row in fresh.execute('PRAGMA integrity_check').fetchall()]
    result['passed'] = (result['sqlite'] == ['ok'] and result['fts'] == 'ok'
                        and result['sqlite_after_fts_check'] == ['ok'])
    result['pooled_connection_warning'] = pooled != ['ok']
    return result


def benchmark_scale(*, rows=100000, positives=1000, iterations=10, batch_size=500):
    if not 1 <= rows <= 1000000 or not 0 <= positives <= rows:
        raise ValueError('require 1..1,000,000 rows and 0..rows positives')
    if not 1 <= batch_size <= db.MAX_SAVE_BATCH_SIZE or not 1 <= iterations <= 200:
        raise ValueError('invalid batch size or iteration count')
    report = dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(),
        scope='disposable fictional rows; actual persistence, evidence and FTS writer; local sequential queries, not HTTP load',
        requested_rows=rows, requested_positives=positives, batch_size=batch_size,
        failures=[], cases=[])
    with tempfile.TemporaryDirectory(prefix='prizescout-scale-') as directory:
        path = Path(directory) / 'fictional.db'
        engine = db.create_db_and_tables(f'sqlite:///{path}')
        try:
            started = time.perf_counter()
            for offset in range(0, rows, batch_size):
                db.save_giveaways_sync(engine, [fictional_row(i, positives)
                    for i in range(offset, min(rows, offset + batch_size))])
            elapsed = time.perf_counter() - started
            report['ingestion'] = dict(seconds=round(elapsed, 3), rows_per_second=round(rows / elapsed, 2))
            report['rows_before_checks'] = counts(engine)
            if report['rows_before_checks']['video'] != rows:
                report['failures'].append('source_count_mismatch')
            cases = [('current_page_1', dict(current_only=True)),
                ('current_page_5', dict(current_only=True, page=5)),
                ('fts_camera', dict(q='camera', current_only=True)),
                ('fts_missing', dict(q='zzscalemissing', current_only=True)),
                ('grouped_current', dict(group_campaigns=True, current_only=True)),
                ('grouped_all', dict(group_campaigns=True))]
            for name, parameters in cases:
                timings, totals = [], set()
                for _ in range(iterations):
                    started = time.perf_counter()
                    result = db.list_index(engine, page_size=20, **parameters)
                    json.dumps(result, default=str)
                    timings.append((time.perf_counter() - started) * 1000)
                    totals.add(result['total'])
                ordered = sorted(timings)
                report['cases'].append(dict(name=name, totals=sorted(totals), iterations=iterations,
                    latency_ms=dict(p50=round(statistics.median(ordered), 3),
                    p95=round(ordered[math.ceil(.95 * len(ordered)) - 1], 3))))
            current_total = report['cases'][0]['totals'][0]
            if positives >= 4 and current_total == 0:
                report['failures'].append('current_fixture_not_discoverable')
            # A real independent writer commits while an explicitly held reader
            # snapshot sees the old count. No sleeps or timing-only assertions.
            with engine.connect() as reader, ThreadPoolExecutor(max_workers=1) as executor:
                reader.execute(text('BEGIN'))
                old_count = reader.execute(text('SELECT COUNT(*) FROM video')).scalar_one()
                started = time.perf_counter()
                executor.submit(db.save_giveaways_sync, engine, [fictional_row(rows, 0)]).result(timeout=30)
                stable = reader.execute(text('SELECT COUNT(*) FROM video')).scalar_one() == old_count
                reader.rollback()
                visible = reader.execute(text('SELECT COUNT(*) FROM video')).scalar_one() == old_count + 1
                report['concurrency'] = dict(reader_snapshot_stable=stable, writer_visible_after_end=visible,
                    writer_commit_ms=round((time.perf_counter() - started) * 1000, 3))
                if not (stable and visible):
                    report['failures'].append('reader_writer_isolation')
            purge_ids = [f'fictional-{i}' for i in range(min(100, rows))]
            # Exercise the actual retention deletion primitive, including derived
            # promotions and FTS triggers; no fixture database is retained.
            db.remove_videos(engine, purge_ids)
            with engine.begin() as connection:
                placeholders = ', '.join(f':v{i}' for i in range(len(purge_ids)))
                params = {f'v{i}': video_id for i, video_id in enumerate(purge_ids)}
                orphans = sum(connection.execute(text(f'SELECT COUNT(*) FROM {table} WHERE video_id IN ({placeholders})'),
                    params).scalar_one() for table in ('video', 'giveaway', 'promotionmention'))
            report['integrity'] = check_integrity(engine, path)
            report['integrity'].update(purged_sources=len(purge_ids), purge_residual_rows=orphans)
            if not report['integrity']['passed'] or orphans:
                report['failures'].append('cleanup_or_integrity')
            report['rows_after_checks'] = counts(engine)
            with engine.begin() as connection:
                connection.execute(text('PRAGMA wal_checkpoint(TRUNCATE)'))
            report['database_bytes'] = path.stat().st_size
        except Exception as exc:
            # No raw source, exception details, database paths or credentials.
            report['failures'].append(type(exc).__name__)
        finally:
            engine.dispose()
    report['passed'] = not report['failures']
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=100000)
    parser.add_argument('--positives', type=int, default=1000)
    parser.add_argument('--iterations', type=int, default=10)
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--out', type=Path, help='new JSON report; existing paths are never overwritten')
    args = parser.parse_args(argv)
    output = None
    try:
        # Reserve exclusively before expensive work, eliminating overwrite races.
        if args.out:
            output = args.out.open('x', encoding='utf-8')
        report = benchmark_scale(rows=args.rows, positives=args.positives,
            iterations=args.iterations, batch_size=args.batch_size)
        rendered = json.dumps(report, indent=2) + '\n'
        if output:
            output.write(rendered)
        print(rendered, end='')
        return 0 if report['passed'] else 1
    except (ValueError, OSError) as exc:
        parser.error(type(exc).__name__ + ': invalid parameters or output unavailable')
    finally:
        if output:
            output.close()


if __name__ == '__main__':
    raise SystemExit(main())
