#!/usr/bin/env python3
"""Rehearse application restart and SQLite restore using disposable fictional data.

Requires the production frontend build unless --skip-ui is supplied. Uses no
network or real index and launches fresh processes to avoid hiding restart bugs
behind a cached SQLAlchemy engine.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _seed(path):
    from datetime import datetime, timedelta, timezone
    from sqlmodel import Session, select
    from src import db
    from src.demo import seed_demo
    from src.extractor import extract

    engine = db.create_db_and_tables(f'sqlite:///{path}')
    seed_demo(engine)
    description = ('An ordinary fictional cooking tutorial. '
                   'Fictional coupon code DEMO20 for 20% off at https://shop.example.com.')
    db.save_giveaway_sync(engine, dict(id='recovery-negative', title='Fictional tutorial',
        description=description), analysis=extract(description))
    db.save_giveaway_sync(engine, dict(id='recovery-stale', title='Fictional stale prize',
        description='Fictional outdated prize source.'), dict(prizes='Camera'))
    with Session(engine) as session:
        stale = session.exec(select(db.Video).where(db.Video.video_id == 'recovery-stale')).one()
        stale.fetched_at = datetime.now(timezone.utc) - timedelta(days=31)
        session.add(stale)
        session.add(db.IngestionState(channel_id='demo-creator-0', page_token='fictional-resume-cursor',
                                      last_succeeded_at=datetime.now(timezone.utc)))
        session.add(db.QuotaUsage(day='2030-01-01', units=123))
        session.commit()
    engine.dispose()


def _probe(check_ui):
    from fastapi.testclient import TestClient
    from sqlmodel import Session, select
    from src import api, db
    from src.main import app

    with TestClient(app) as client:
        health = client.get('/api/health')
        assert health.status_code == 200
        assert health.json()['indexed_videos'] == 20
        assert health.json()['ingestion']['enabled'] is False
        data = client.get('/api/giveaways?group_campaigns=true').json()
        assert data['total'] == 17
        assert all(result['is_demo'] for result in data['results'])
        assert all(result['video_id'] != 'recovery-stale' for result in data['results'])
        current = client.get('/api/giveaways?current_only=true&group_campaigns=true').json()
        assert current['total'] == 17
        assert all(row['availability_assessment']['state'] == 'source_reported_open' for row in current['results'])
        promotions = client.get('/api/promotions').json()
        assert promotions['total'] == 1
        assert promotions['results'][0]['code'] == 'DEMO20'
        assert promotions['results'][0]['availability'] == 'unverified'
        assert promotions['results'][0]['last_checked_at'] is None
        assert promotions['results'][0]['redemption_verified'] is False
        found = client.get('/api/search?q=espresso').json()
        assert found['total'] == 1 and found['results'][0]['video_id'] == 'demo-15'
        books = client.get('/api/search?category=books&group_campaigns=true').json()
        assert books['total'] == 1
        assert books['results'][0]['campaign']['source_count'] == 2
        assert books['results'][0]['evidence']['prizes']
        coverage = client.get('/api/coverage').json()['totals']
        assert coverage['indexed_videos'] == 19 and coverage['stale_videos'] == 1
        assert coverage['analyzed_videos'] == 19
        if check_ui:
            import re
            page = client.get('/')
            assert page.status_code == 200 and 'text/html' in page.headers['content-type']
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text)
            assert assets, 'Production frontend assets not found; build frontend first.'
            assert all(client.get(asset).status_code == 200 for asset in assets)
    with Session(api.get_engine()) as session:
        assert session.get(db.QuotaUsage, '2030-01-01').units == 123
        assert session.get(db.IngestionState, 'demo-creator-0').page_token == 'fictional-resume-cursor'
        # Fingerprint all persisted state without exposing fictional/raw source text
        # or cursors in the report. A replay that renews timestamps would differ.
        state = [[row.model_dump(mode='json') for row in session.exec(select(model).order_by(*model.__table__.primary_key.columns)).all()]
                 for model in (db.Video, db.Giveaway, db.PromotionMention, db.IngestionState, db.QuotaUsage)]
    digest = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
    api.get_engine().dispose()
    return dict(snapshot_sha256=digest, grouped_results=17, fresh_inputs=19)


def _fresh_process(path, check_ui):
    environment = os.environ | dict(DATABASE_URL=f'sqlite:///{path}', INGESTION_ENABLED='0',
                                    YOUTUBE_API_KEY='', PRIZESCOUT_DEMO='1')
    command = [sys.executable, str(Path(__file__).resolve()), '--probe']
    if not check_ui:
        command.append('--skip-ui')
    completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60)
    if completed.returncode:
        raise RuntimeError('Release probe failed:\n' + completed.stderr)
    return json.loads(completed.stdout)


def rehearse(check_ui=True):
    from scripts.backup_db import backup

    with tempfile.TemporaryDirectory(prefix='prizescout-release-') as directory:
        source = Path(directory) / 'original.db'
        saved = Path(directory) / 'backup.db'
        restored = Path(directory) / 'restored.db'
        _seed(source)
        initial = _fresh_process(source, check_ui)
        restarted = _fresh_process(source, check_ui)
        backup(source, saved)
        backup(saved, restored)
        recovered = _fresh_process(restored, check_ui)
        assert initial == restarted == recovered, 'Persisted state differs after restart or restore.'
    return dict(schema_version=1, dataset='disposable fictional demo', network_requests=0,
        checks=dict(fresh_process_restart=True, sqlite_backup_restore=True, persisted_state_equal=True,
                    full_text_search=True, campaign_grouping=True, source_evidence=True,
                    current_discovery=True, unverified_promotions=True,
                    stale_source_hidden=True, negative_inputs_retained=True, quota_and_cursor_retained=True,
                    ingestion_disabled=True, production_ui=check_ui), **initial)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-ui', action='store_true', help='API/recovery only; frontend build not required')
    parser.add_argument('--probe', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    print(json.dumps(_probe(not args.skip_ui) if args.probe else rehearse(not args.skip_ui), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
