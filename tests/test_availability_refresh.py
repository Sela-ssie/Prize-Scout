"""Availability leases share ingestion quota and never renew on a failed request."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlmodel import Session, select

from src import db, ingestion
from src.youtube_client import YouTubeClient

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
DEADLINE = '2026-10-31T23:59:59+00:00'
DESCRIPTION = 'I am giving away a signed book! Comment to enter.\nGiveaway ends October 31, 2026.'


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion, 'datetime', FrozenDateTime)
    monkeypatch.setattr(db, '_utcnow', lambda: NOW)
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path / "refresh.db"}')
    yield engine
    engine.dispose()


def seed(engine, identifier, hours, *, supported=True, deadline=DEADLINE):
    db.save_giveaway_sync(engine, dict(id=identifier, title='Signed book giveaway',
        description=DESCRIPTION if supported else 'An ordinary walk.', published_at='2026-10-16T00:00:00Z'),
        dict(prizes='Signed book', deadline=deadline, confidence=.9, availability='active') if supported else None)
    with Session(engine) as session:
        row = session.exec(select(db.Video).where(db.Video.video_id == identifier)).one()
        row.fetched_at = NOW - timedelta(hours=hours)
        session.add(row)
        session.commit()


def fetched(engine, identifier):
    with Session(engine) as session:
        row = session.exec(select(db.Video).where(db.Video.video_id == identifier)).first()
        return row.fetched_at if row else None


def details(ids):
    return {'items': [dict(id=identifier, snippet=dict(title='Signed book giveaway', description=DESCRIPTION,
        publishedAt='2026-10-16T00:00:00Z', channelId='UCsource', channelTitle='Source')) for identifier in ids]}


def run_ingest(engine, handler, *, watchlist=None, max_units=2, daily_max_units=100):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = YouTubeClient('mock-only-key', client=http, max_attempts=1)
            return await ingestion.ingest_once(engine, watchlist or [], client=client,
                max_units=max_units, daily_max_units=daily_max_units)
    return asyncio.run(run())


def test_recheck_boundaries_and_passed_deadline(engine):
    for hours in (35, 36, 37, 49):
        seed(engine, str(hours), hours)
    seed(engine, 'passed', 49)
    with Session(engine) as session:
        giveaway = session.exec(select(db.Giveaway).where(db.Giveaway.video_id == 'passed')).one()
        giveaway.deadline = (NOW - timedelta(seconds=1)).isoformat()
        session.add(giveaway)
        session.commit()
    assert ingestion.availability_recheck_ids(engine) == ['49', '37', '36']


def test_supported_refresh_uses_first_batch_before_older_ordinary_rows(engine):
    # Fill one actual API batch with supported rows; an older ordinary row must
    # wait when the refresh half-budget only permits one request.
    for index in range(50):
        seed(engine, f'supported-{index:02}', 37)
    seed(engine, 'ordinary-old', 28 * 24, supported=False)
    original_old = fetched(engine, 'ordinary-old')
    calls = []
    def handler(request):
        ids = request.url.params['id'].split(',')
        calls.append(ids)
        return httpx.Response(200, json=details(ids))
    report = run_ingest(engine, handler)
    assert len(calls) == 1 and len(calls[0]) == 50
    assert all(identifier.startswith('supported-') for identifier in calls[0])
    assert report['videos_saved'] == 50 and report['quota_units'] == 1
    assert fetched(engine, 'supported-00') == NOW.replace(tzinfo=None)
    assert fetched(engine, 'ordinary-old') == original_old
    assert not report['errors']


def test_failed_check_never_renews_lease_and_purge_still_runs(engine):
    seed(engine, 'needs-check', 49)
    seed(engine, 'beyond-retention', 31 * 24, supported=False)
    original = fetched(engine, 'needs-check')
    calls = []
    def handler(request):
        calls.append(request.url.params['id'])
        return httpx.Response(403, text='mock-only-key must never be surfaced')
    report = run_ingest(engine, handler)
    assert len(calls) == 1 and calls[0].split(',')[0] == 'needs-check'
    assert report['quota_units'] == 1 and report['removed'] == 1
    assert report['errors'] and 'mock-only-key' not in str(report)
    assert fetched(engine, 'needs-check') == original
    assert fetched(engine, 'beyond-retention') is None
    assert db.list_index(engine, current_only=True)['total'] == 0
    assessment = db.list_index(engine)['results'][0]['availability_assessment']
    assert assessment['state'] == 'needs_recheck' and assessment['discoverable'] is False


@pytest.mark.parametrize('run_cap,daily_cap,expected_calls,expected_units', [(3, 100, 3, 3), (3, 2, 2, 2), (2, 100, 1, 1)])
def test_refresh_and_playlist_share_per_run_and_daily_quota(engine, run_cap, daily_cap, expected_calls, expected_units):
    seed(engine, 'refresh', 37)
    calls = []
    def handler(request):
        endpoint = request.url.path.rsplit('/', 1)[-1]
        calls.append(endpoint)
        if endpoint == 'playlistItems':
            return httpx.Response(200, json={'items': [{'contentDetails': {'videoId': 'new-upload'}}]})
        return httpx.Response(200, json=details(request.url.params['id'].split(',')))
    report = run_ingest(engine, handler, max_units=run_cap, daily_max_units=daily_cap,
        watchlist=[dict(channel_id='UCsource', uploads_playlist_id='UUsource')])
    assert calls == ['videos', 'playlistItems', 'videos'][:expected_calls]
    assert len(calls) == expected_calls
    assert report['quota_units'] == expected_units
    with Session(engine) as session:
        usage = session.exec(select(db.QuotaUsage)).one()
        assert usage.units == expected_units
    if daily_cap == 2 or run_cap == 2:
        assert report['errors'] and report['pages'] == 0
        assert fetched(engine, 'new-upload') is None
    else:
        assert not report['errors'] and report['pages'] == 1
        assert fetched(engine, 'new-upload') is not None
