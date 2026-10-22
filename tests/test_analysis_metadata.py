"""Replay must preserve source age while retaining positive and negative decisions."""
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from src import db
from src.extractor import extract, giveaway_payload


def test_negative_analysis_survives_and_refresh_invalidates_old_prediction(tmp_path):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path / "analysis.db"}')
    video = dict(id='ordinary', title='A quiet day', description='I made breakfast.')
    analysis = extract(video['description'], title=video['title'])
    assert analysis['is_giveaway'] is False
    db.save_giveaway_sync(engine, video, giveaway_payload(analysis), analysis=analysis)
    with Session(engine) as session:
        stored = session.exec(select(db.Video)).one()
        assert stored.extraction_is_giveaway is False
        assert stored.extractor_version == analysis['extractor_version']
        assert stored.extraction_reason == analysis['rejected_because']
        assert stored.extracted_at is not None
        assert not session.exec(select(db.Giveaway)).all()
    # New source content without a new analysis must not inherit an old verdict.
    db.save_giveaway_sync(engine, video | {'description': 'Changed source'})
    with Session(engine) as session:
        stored = session.exec(select(db.Video)).one()
        assert stored.extraction_is_giveaway is None
        assert stored.extractor_version is None
        assert stored.extracted_at is None


def test_offline_replay_removes_old_positive_without_renewing_source_age(tmp_path):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path / "replay.db"}')
    db.save_giveaway_sync(engine, dict(id='changed', description='Ordinary cooking tips.'),
                          {'prizes': 'A camera'})
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    with Session(engine) as session:
        stored = session.exec(select(db.Video)).one()
        stored.fetched_at = old
        session.add(stored)
        session.commit()
    analysis = extract('Ordinary cooking tips.')
    assert db.update_extraction_sync(engine, 'changed', giveaway_payload(analysis), analysis=analysis)
    with Session(engine) as session:
        stored = session.exec(select(db.Video)).one()
        assert stored.fetched_at == old
        assert stored.extraction_is_giveaway is False
        assert not session.exec(select(db.Giveaway)).all()
    assert not db.update_extraction_sync(engine, 'missing', None, analysis=analysis)
