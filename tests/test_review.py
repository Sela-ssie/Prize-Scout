"""Offline review packets preserve connected groups and do not fabricate labels."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import MetaData, text
from sqlmodel import Session

from src.db import Giveaway, Video, create_sync_engine
from src.review import build_review_queue
from scripts.prepare_review import export_review

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.fixture
def engine(tmp_path):
    engine = create_sync_engine('sqlite:///' + str(tmp_path / 'review.db'))
    metadata = MetaData()
    table = Video.__table__.to_metadata(metadata)
    table.c.fetched_at.nullable = True  # Legacy retained rows may lack fetch dates.
    Giveaway.__table__.to_metadata(metadata)
    metadata.create_all(engine)
    return engine


def add(engine, video_id, *, description=None, campaign=None, fetched_at=NOW, **extra):
    with Session(engine) as session:
        session.add(Video(video_id=video_id, title='Title ' + video_id,
                          raw_description=description or 'Unique description ' + video_id,
                          channel_id=extra.pop('channel_id', 'channel-' + video_id),
                          fetched_at=fetched_at, **extra))
        if campaign:
            session.add(Giveaway(video_id=video_id, campaign_key=campaign))
        session.commit()


def test_transitive_campaign_and_normalized_duplicate_group(engine):
    add(engine, 'a', description='Enter THIS contest!', campaign='campaign-a')
    add(engine, 'b', description=' enter this   CONTEST ', campaign='campaign-b')
    add(engine, 'c', description='Different copy', campaign='campaign-b')
    result = build_review_queue(engine, limit=3, now=NOW)
    assert len(result['items']) == 3
    assert {item['group_id'] for item in result['items']} == {'campaign-a'}
    assert len({item['suggested_split'] for item in result['items']}) == 1
    assert build_review_queue(engine, limit=2, now=NOW)['items'] == []
    assert build_review_queue(engine, reviewed_ids={'c'}, now=NOW)['items'] == []


def test_supported_campaign_links_group_negatives_without_giveaway_rows(engine):
    add(engine, 'a', description='Enter https://gleam.io/abc12/foo?ref=1')
    add(engine, 'b', description='Another title https://gleam.io/abc12/bar?ref=2')
    result = build_review_queue(engine, now=NOW)
    assert len({item['group_id'] for item in result['items']}) == 1


def test_freshness_boundary_and_missing_date_excluded_without_writes(engine):
    add(engine, 'fresh', fetched_at=NOW - timedelta(days=30))
    add(engine, 'stale', fetched_at=NOW - timedelta(days=30, microseconds=1))
    add(engine, 'missing')
    add(engine, 'future', fetched_at=NOW + timedelta(seconds=1))
    with engine.begin() as connection:
        connection.execute(text("UPDATE video SET fetched_at=NULL WHERE video_id='missing'"))
    result = build_review_queue(engine, now=NOW)
    assert [item['video_id'] for item in result['items']] == ['fresh']
    assert result['manifest']['exclusions']['missing_fetched_at'] == 1
    with Session(engine) as session:
        assert session.get(Video, 2).fetched_at == (NOW - timedelta(days=30, microseconds=1)).replace(tzinfo=None)
    with pytest.raises(ValueError, match='timezone-aware'):
        build_review_queue(engine, now=NOW.replace(tzinfo=None))


def test_determinism_bounds_partition_stability_and_export_blinding(engine, tmp_path):
    for i in range(35):
        add(engine, str(i))
    result = build_review_queue(engine, seed='sample', limit=30, now=NOW)
    assert result == build_review_queue(engine, seed='sample', limit=30, now=NOW)
    other = build_review_queue(engine, seed='other', limit=35, now=NOW)
    split_by_id = {item['video_id']: item['suggested_split'] for item in other['items']}
    assert len(result['items']) == 30
    assert all(item['suggested_split'] == split_by_id[item['video_id']] for item in result['items'])
    assert any(item['suggested_split'] == 'test' for item in result['items'])
    output = export_review(result, tmp_path / 'packet')
    exported_manifest = json.loads((output / 'manifest.json').read_text())
    assert 'eligible_bucket_counts' not in exported_manifest
    assert 'selected_balance' not in exported_manifest
    assert 'sampling_audit' not in exported_manifest
    assert not (output / 'labels.jsonl').exists()
    assignments = [json.loads(line) for line in (output / 'assignments.jsonl').read_text().splitlines()]
    assert all('prediction' not in item and 'source' not in item for item in assignments if item['suggested_split'] == 'test')
    corpus = [json.loads(line) for line in (output / 'corpus.jsonl').read_text().splitlines()]
    assert all('prediction' not in item for item in corpus)
    assert 'description' not in (output / 'manifest.json').read_text().replace('descriptions', '')
    for item in result['items']:
        assert item['source_snapshot_sha256'] == hashlib.sha256(json.dumps(item['source'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with pytest.raises(FileExistsError):
        export_review(result, output)


def test_stratifies_positive_negative_and_rejections(engine):
    for bucket in ('positive', 'negative', 'no_entry_instructions'):
        for i in range(8):
            add(engine, bucket + str(i), channel_id='same-channel',
                extraction_is_giveaway=bucket == 'positive',
                extraction_reason=bucket if bucket == 'no_entry_instructions' else None)
    result = build_review_queue(engine, limit=3, now=NOW)
    assert {row['bucket'] for row in result['sampling_audit']['selected_balance']} == {'positive', 'negative', 'rejection:no_entry_instructions'}


def test_export_cli_never_creates_missing_database(tmp_path, monkeypatch):
    from scripts.prepare_review import main
    path = tmp_path / 'missing.db'
    destination = tmp_path / 'packet'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{path}')
    with pytest.raises(SystemExit):
        main(['--out-dir', str(destination)])
    assert not path.exists()
    assert not destination.exists()
