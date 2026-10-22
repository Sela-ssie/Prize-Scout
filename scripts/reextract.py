#!/usr/bin/env python3
"""Re-extract retained raw video text offline without renewing cache timestamps."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlmodel import Session, select
from src import db
from src.extractor import extract, giveaway_payload
from src.ingestion import stale_ids
from src.worker import ingestion_lock


def reextract(engine, dry_run=False):
    report = {'processed': 0, 'giveaways': 0, 'removed_stale': 0, 'dry_run': dry_run}
    expired = stale_ids(engine, days=30)
    if not dry_run:
        db.remove_videos(engine, expired)
        db.purge_legacy_archives(engine)
    report['removed_stale'] = len(expired)
    last_id = 0
    while True:
        with Session(engine) as session:
            rows = session.exec(select(db.Video).where(db.Video.id > last_id).order_by(db.Video.id).limit(200)).all()
        if not rows:
            break
        for video in rows:
            last_id = video.id
            if video.video_id in expired:
                continue
            analysis = extract(video.raw_description or '',
                title=video.title, published_at=video.published_at)
            extracted = giveaway_payload(analysis)
            if not dry_run:
                db.update_extraction_sync(engine, video.video_id, extracted, analysis=analysis)
            report['processed'] += 1
            report['giveaways'] += bool(extracted)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='preview extraction counts without changing rows')
    args = parser.parse_args()
    engine = db.create_db_and_tables(os.getenv('DATABASE_URL', 'sqlite:///./prizes.db'))
    try:
        with ingestion_lock(engine):
            print(json.dumps(reextract(engine, dry_run=args.dry_run)))
    finally:
        engine.dispose()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
