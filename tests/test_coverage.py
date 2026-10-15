"""Coverage is current index inventory, never a legacy negative or quality score."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session

from scripts.diagnose import main
from src.coverage import coverage_summary, diagnostic_report
from src.db import Giveaway, IngestionState, QuotaUsage, Video, create_db_and_tables

NOW = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)


@pytest.fixture
def engine(tmp_path):
    engine = create_db_and_tables(f'sqlite:///{tmp_path / "coverage.db"}')
    yield engine
    engine.dispose()


def seed(engine):
    with Session(engine) as session:
        for identifier, days, prediction, channel in [
            ('active', 0, True, 'one'), ('duplicate', 1, True, 'one'),
            ('unknown', 2, None, 'two'), ('expired', 3, True, 'two'),
            ('negative', 30, False, 'one'), ('stale', 31, True, 'old'),
        ]:
            session.add(Video(video_id=identifier, channel_id=channel,
                channel_title='STALE SECRET' if channel == 'old' else f'Channel {channel}',
                title='RAW TITLE SECRET', raw_description='RAW DESCRIPTION SECRET',
                fetched_at=NOW - timedelta(days=days), extraction_is_giveaway=prediction,
                extraction_reason='not_giveaway' if prediction is False else None,
                extractor_version='test-v1' if prediction is not None else None,
                extracted_at=NOW if prediction is not None else None))
        for identifier, deadline, availability, campaign in [
            ('active', '2026-10-15T00:00:00Z', 'unknown', 'shared'),
            ('duplicate', '2026-10-15T00:00:00Z', 'active', 'shared'),
            ('unknown', None, 'active', None),
            ('expired', NOW.isoformat(), 'active', None),
            ('stale', '2026-11-01T00:00:00Z', 'active', None),
        ]:
            session.add(Giveaway(video_id=identifier, deadline=deadline,
                availability=availability, campaign_key=campaign, categories_json='["tech", "tech"]'))
        session.add(IngestionState(channel_id='empty', channel_title='Empty configured source',
                                   completed_at=NOW, last_succeeded_at=NOW))
        session.add(QuotaUsage(day='2026-10-13', units=17))
        session.add(QuotaUsage(day='2026-10-14', units=99))
        session.commit()


def test_summary_freshness_analysis_availability_and_privacy(engine):
    seed(engine)
    report = coverage_summary(engine, NOW)
    assert report['totals'] == dict(indexed_videos=5, stale_videos=1, analyzed_videos=4,
        unanalyzed_videos=1, detected_mentions=4, available_mentions=2,
        unknown_availability_mentions=1, expired_mentions=1, campaigns=2, source_count=4)
    sources = {row['channel_id']: row for row in report['sources']}
    assert sources['one']['fresh_videos'] == 3  # Exact thirty-day cutoff is fresh.
    assert sources['empty']['fresh_videos'] == 0
    assert sources['empty']['backfill_complete'] is True
    assert sources['empty']['last_ingested_at'] == NOW.isoformat()
    assert sources['old']['channel_title'] == 'old'
    assert sources['old']['last_fetched_at'] is None
    assert sources['two']['analyzed_videos'] == 1
    assert next(row['count'] for row in report['categories'] if row['id'] == 'tech') == 3
    serialized = json.dumps(report)
    for forbidden in ['SECRET', 'raw_description', 'extraction_reason', 'page_token', 'extractor_version']:
        assert forbidden not in serialized


def test_clock_changes_deadlines_and_local_quota_uses_pacific_day(engine):
    seed(engine)
    diagnostic = diagnostic_report(engine, NOW)
    assert diagnostic['quota'] == dict(day='2026-10-13', timezone='America/Los_Angeles', units=17)
    assert diagnostic['rejection_reasons'] == {'not_giveaway': 1, 'unknown': 0}
    assert diagnostic['extractor_versions'] == {'test-v1': 4, 'unknown': 1}
    assert diagnostic['input_coverage']['unknown_predictions'] == 1
    assert diagnostic['input_coverage']['with_nonempty_description'] == 5
    assert coverage_summary(engine, NOW + timedelta(days=2))['totals']['available_mentions'] == 0
    assert 'RAW DESCRIPTION SECRET' not in json.dumps(diagnostic)


def test_empty_summary_is_serializable_and_zero(engine):
    report = coverage_summary(engine, NOW.replace(tzinfo=None))
    assert all(value == 0 for value in report['totals'].values())
    assert report['sources'] == []
    assert report['generated_at'] == NOW.isoformat()
    json.dumps(report)


def test_diagnosis_never_creates_database_and_refuses_overwrite(tmp_path, monkeypatch, engine):
    missing = tmp_path / 'missing.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{missing}')
    with pytest.raises(SystemExit):
        main([])
    assert not missing.exists()
    monkeypatch.setenv('DATABASE_URL', str(engine.url))
    destination = tmp_path / 'diagnostic.json'
    assert main(['--now', NOW.isoformat(), '--summary-only', '--out', str(destination)]) == 0
    before = destination.read_bytes()
    assert json.loads(before)['generated_at'] == NOW.isoformat()
    with pytest.raises(SystemExit):
        main(['--out', str(destination)])
    assert destination.read_bytes() == before


def test_invalid_deadline_is_unknown_and_explicit_expiry_wins(engine):
    with Session(engine) as session:
        for video_id, deadline, availability in [
            ('invalid', 'not a date', 'active'),
            ('closed', '2027-01-01T00:00:00Z', 'expired'),
        ]:
            session.add(Video(video_id=video_id, fetched_at=NOW))
            session.add(Giveaway(video_id=video_id, deadline=deadline, availability=availability))
        session.commit()
    report = coverage_summary(engine, NOW)
    assert report['totals']['available_mentions'] == 0
    assert report['totals']['unknown_availability_mentions'] == 1
    assert report['totals']['expired_mentions'] == 1
