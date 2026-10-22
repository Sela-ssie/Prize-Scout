"""Bounded durable source writes and SQLite reader/writer isolation."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text
from sqlmodel import Session, select

from src import db


def record(i):
    return dict(video=dict(id=f'fictional-{i}', channel_id=f'channel-{i % 20}',
        title='Fictional workshop update', description='A fictional workshop tour and camera setup.'),
        analysis=dict(is_giveaway=False, confidence=.1, extractor_version='scale-fixture'))


def test_thousand_records_commit_once_and_replay_preserves_age(tmp_path):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path / "scale.db"}')
    commits = []
    event.listen(engine, 'commit', lambda connection: commits.append(True))
    assert db.save_giveaways_sync(engine, [record(i) for i in range(1000)]) == 1000
    assert len(commits) == 1
    with Session(engine) as session:
        assert len(session.exec(select(db.Video)).all()) == 1000
        video = session.exec(select(db.Video).where(db.Video.video_id == 'fictional-0')).one()
        old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        video.fetched_at = old
        session.add(video)
        session.commit()
    assert db.update_extraction_sync(engine, 'fictional-0', None, analysis=record(0)['analysis'])
    with Session(engine) as session:
        video = session.exec(select(db.Video).where(db.Video.video_id == 'fictional-0')).one()
        assert video.fetched_at == old
        assert video.extraction_is_giveaway is False
        assert video.raw_description
    engine.dispose()


def test_failed_batch_rolls_back_sources_extractions_promotions_and_fts(tmp_path):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path / "rollback.db"}')
    first = record(0)
    first['video']['description'] = 'Use code FICTIONAL for 10% off. Fictional camera giveaway.'
    first['extracted'] = dict(prizes='camera', confidence=.8)
    with pytest.raises(ValueError):
        db.save_giveaways_sync(engine, [first, dict(video={})])
    with engine.connect() as connection:
        for table in ('video', 'giveaway', 'promotionmention', 'video_fts'):
            assert connection.execute(text(f'SELECT COUNT(*) FROM {table}')).scalar_one() == 0
    with pytest.raises(ValueError):
        db.save_giveaways_sync(engine, [record(i) for i in range(1001)])
    with pytest.raises(ValueError):
        db.save_giveaways_sync(engine, iter([record(0)]))
    engine.dispose()


def test_wal_reader_snapshot_does_not_block_writer(tmp_path):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path / "wal.db"}')
    with engine.connect() as reader:
        assert reader.execute(text('PRAGMA journal_mode')).scalar_one() == 'wal'
        assert reader.execute(text('PRAGMA foreign_keys')).scalar_one() == 1
        assert reader.execute(text('PRAGMA busy_timeout')).scalar_one() == 30000
        # Explicit BEGIN is needed with sqlite3 legacy SELECT transaction mode.
        reader.execute(text('BEGIN'))
        assert reader.execute(text('SELECT COUNT(*) FROM video')).scalar_one() == 0
        db.save_giveaways_sync(engine, [record(0)])
        assert reader.execute(text('SELECT COUNT(*) FROM video')).scalar_one() == 0
        reader.rollback()
        assert reader.execute(text('SELECT COUNT(*) FROM video')).scalar_one() == 1
    engine.dispose()


def test_batch_lifecycle_matches_single_writer_including_evidence_and_closure(tmp_path):
    from scripts.benchmark_scale import fictional_row
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path / "lifecycle.db"}')
    db.save_giveaways_sync(engine, [fictional_row(i, 4) for i in range(4)])
    with Session(engine) as session:
        assert len(session.exec(select(db.Giveaway)).all()) == 4
        assert session.exec(select(db.PromotionMention)).all()
        closed = session.exec(select(db.Video).where(db.Video.video_id == 'fictional-1')).one()
        assert closed.source_reports_closed
        positive = session.exec(select(db.Giveaway).where(db.Giveaway.video_id == 'fictional-2')).one()
        assert positive.current_evidence_supported
        assert 'Giveaway ends' in positive.evidence_json
    # Closed companion suppresses both sources, leaving the other campaign open.
    assert db.list_index(engine, current_only=True)['total'] == 2
    assert db.list_index(engine, current_only=True, group_campaigns=True)['total'] == 1
    negative = record(0)
    db.save_giveaways_sync(engine, [negative])
    with Session(engine) as session:
        assert not session.exec(select(db.Giveaway).where(db.Giveaway.video_id == 'fictional-0')).all()
        assert not session.exec(select(db.PromotionMention).where(db.PromotionMention.video_id == 'fictional-0')).all()
        video = session.exec(select(db.Video).where(db.Video.video_id == 'fictional-0')).one()
        assert video.extraction_is_giveaway is False
        assert not video.source_reports_closed
    engine.dispose()


def test_startup_does_not_rebuild_fts_or_rescan_unique_source_rows(tmp_path):
    path = f'sqlite:///{tmp_path / "startup.db"}'
    engine = db.create_db_and_tables(path)
    db.save_giveaways_sync(engine, [record(0)])
    engine.dispose()
    statements = []
    # Engine class listener captures the new factory-created engine's migration.
    from sqlalchemy.engine import Engine
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(Engine, 'before_cursor_execute', capture)
    try:
        reopened = db.create_db_and_tables(path)
        reopened.dispose()
    finally:
        event.remove(Engine, 'before_cursor_execute', capture)
    assert not any("VALUES('rebuild')" in statement for statement in statements)
    assert not any('HAVING COUNT(*) > 1' in statement for statement in statements)


def test_independent_integrity_checks_detect_real_external_content_drift(tmp_path):
    from scripts.benchmark_scale import check_integrity
    path = tmp_path / 'integrity.db'
    engine = db.create_db_and_tables(f'sqlite:///{path}')
    db.save_giveaways_sync(engine, [record(0)])
    healthy = check_integrity(engine, path)
    assert healthy['passed']
    assert healthy['sqlite'] == ['ok']
    # Deliberately remove an index entry while retaining its external source.
    # Structural checks alone can pass; explicit rank=1 must reject the drift.
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO video_fts(video_fts,rowid,title,raw_description) "
            "SELECT 'delete',id,title,raw_description FROM video WHERE video_id='fictional-0'"))
    broken = check_integrity(engine, path)
    assert not broken['passed']
    assert broken['fts'] != 'ok'
    engine.dispose()
