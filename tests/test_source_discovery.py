"""Background YouTube discovery uses durable cursors and bounded real-client calls."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlmodel import Session, select

from src import db, discovery, ingestion
from src.ingestion import QuotaGuard
from src.youtube_client import YouTubeClient

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def vid(number):
    return f'v{number:010d}'


def channel(number):
    return f'UC{number:022d}'


def search(ids=(), token=None):
    payload = {'items': [{'id': {'kind': 'youtube#video', 'videoId': identity},
                           'snippet': {'description': 'Truncated search text'}} for identity in ids]}
    if token is not None:
        payload['nextPageToken'] = token
    return httpx.Response(200, json=payload)


def details(request):
    return httpx.Response(200, json={'items': [
        {'id': identity, 'snippet': {
            'title': 'A walk around town', 'description': f'Full ordinary description for {identity}.',
            'channelId': channel(int(identity[1:])), 'channelTitle': f'Creator {identity}',
            'publishedAt': '2026-10-16T00:00:00Z'}}
        for identity in request.url.params['id'].split(',')]})


@pytest.fixture
def index(tmp_path, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(discovery, '_now', lambda: clock[0])
    monkeypatch.setattr(db, '_utcnow', lambda: clock[0])
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path / "sources.db"}')
    yield engine, clock
    engine.dispose()


def run(engine, handler, queries=('giveaway',), **kwargs):
    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = YouTubeClient('test-secret', client=http, sleep=lambda _: asyncio.sleep(0))
            return await discovery.discover_once(engine, queries, client=client, **kwargs)
    return asyncio.run(collect())


def query_state(engine, query='giveaway'):
    with Session(engine) as session:
        return session.get(discovery.DiscoveryQuery, query)


def test_broad_search_fetches_full_negative_details_and_grows_beyond_ten(index):
    engine, _ = index
    requests = []
    def handle(request):
        requests.append(request)
        return search([vid(n) for n in range(12)], 'next') if request.url.path.endswith('/search') else details(request)
    report = run(engine, handle)
    params = requests[0].url.params
    assert params['type'] == 'video' and params['maxResults'] == '50'
    assert params['order'] == 'date' and params['q'] == 'giveaway'
    assert datetime.fromisoformat(params['publishedAfter']) == NOW - timedelta(days=14)
    assert 'channelId' not in params and 'pageToken' not in params
    assert requests[1].url.path.endswith('/videos')
    assert report['videos_saved'] == 12 and report['pages'] == 1 and not report['errors']
    assert report['sources_added'] == 12
    with Session(engine) as session:
        videos = session.exec(select(db.Video)).all()
        assert len(videos) == 12 and not session.exec(select(db.Giveaway)).all()
        assert all(row.raw_description.startswith('Full ordinary description') for row in videos)
        assert all(row.extraction_is_giveaway is False and row.extractor_version for row in videos)
        assert len(session.exec(select(discovery.DiscoveredSource)).all()) == 12
        assert len(session.exec(select(db.IngestionState)).all()) == 12
    curated = [{'channel_id': channel(0), 'title': 'Curated title', 'uploads_playlist_id': 'UU'+'0'*22},
               {'channel_id': channel(20), 'title': 'Extra curated'}]
    merged = discovery.merged_watchlist(engine, curated)
    assert len(merged) == 13
    assert next(row for row in merged if row['channel_id'] == channel(0)) == curated[0]


def test_duplicates_are_fetched_and_stored_once_and_repeat_is_idempotent(index):
    engine, _ = index
    fetched = []
    def handle(request):
        if request.url.path.endswith('/search'):
            return search([vid(1), vid(1)], 'repeated')
        fetched.append(request.url.params['id'])
        return details(request)
    for _ in range(2):
        report = run(engine, handle)
        assert report['videos_saved'] == 1
        assert report['sources_added'] == (1 if not fetched[:-1] else 0)
    assert fetched == [vid(1), vid(1)]
    with Session(engine) as session:
        assert len(session.exec(select(db.Video)).all()) == 1
        assert len(session.exec(select(discovery.DiscoveredSource)).all()) == 1
    assert query_state(engine).page_token == 'repeated'


def test_crash_during_persistence_does_not_advance_page(index, monkeypatch):
    engine, _ = index
    def handle(request):
        return search([vid(1)], 'next') if request.url.path.endswith('/search') else details(request)
    original = ingestion.persist_rows
    def crash(*args):
        raise RuntimeError('simulated database failure')
    monkeypatch.setattr(ingestion, 'persist_rows', crash)
    with pytest.raises(RuntimeError, match='simulated database failure'):
        run(engine, handle)
    assert query_state(engine).page_token is None
    assert query_state(engine).last_succeeded_at is None
    monkeypatch.setattr(ingestion, 'persist_rows', original)
    assert run(engine, handle)['pages'] == 1
    assert query_state(engine).page_token == 'next'


def test_failed_request_keeps_cursor_and_rotates_to_next_query(index):
    engine, _ = index
    with Session(engine) as session:
        session.add(discovery.DiscoveryQuery(query='first', page_token='parked',
            published_after=(NOW-timedelta(days=14)).isoformat(), window_started_at=NOW))
        session.commit()
    calls = []
    def handle(request):
        calls.append(request.url.params['q'])
        assert request.url.params.get('pageToken') == ('parked' if calls[-1] == 'first' else None)
        return httpx.Response(403)
    first = run(engine, handle, queries=['first', 'second'], max_search_calls=1)
    second = run(engine, handle, queries=['first', 'second'], max_search_calls=1)
    assert first['errors'] and second['errors'] and calls == ['first', 'second']
    assert query_state(engine, 'first').page_token == 'parked'


def test_explicit_invalid_page_token_resets_cursor(index):
    engine, _ = index
    with Session(engine) as session:
        session.add(discovery.DiscoveryQuery(query='giveaway', page_token='expired',
            published_after=(NOW-timedelta(days=14)).isoformat(), window_started_at=NOW))
        session.commit()
    response = httpx.Response(400, json={'error': {'errors': [{'reason': 'invalidPageToken'}]}})
    assert run(engine, lambda _: response)['errors']
    assert query_state(engine).page_token is None
    assert query_state(engine).last_succeeded_at is None


@pytest.mark.parametrize('payload', [None, {}, {'items': {}}, {'items': [None]},
    {'items': [{'id': {'kind': 'youtube#channel', 'channelId': channel(1)}}]},
    {'items': [{'id': {'kind': 'youtube#video', 'videoId': 'bad'}}]},
    {'items': [], 'nextPageToken': 3}])
def test_malformed_search_never_advances_cursor(index, payload):
    engine, _ = index
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=payload)
    report = run(engine, handle)
    assert report['errors'] and report['pages'] == 0
    assert len(requests) == 1
    assert query_state(engine).completed_at is None
    assert query_state(engine).last_succeeded_at is None


def test_valid_empty_search_envelope_completes_without_detail_request(index):
    engine, _ = index
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={'kind': 'youtube#searchListResponse', 'pageInfo': {'totalResults': 0}})
    report = run(engine, handle)
    assert report['pages'] == 1 and not report['errors'] and len(calls) == 1
    assert query_state(engine).completed_at is not None


def test_search_retry_attempts_have_separate_durable_daily_quota(index):
    engine, _ = index
    calls = []
    def fail(request):
        calls.append(request)
        return httpx.Response(503)
    first = run(engine, fail, max_search_calls=2, daily_max_search_calls=3)
    second = run(engine, fail, max_search_calls=2, daily_max_search_calls=3)
    assert first['search_calls'] == 2 and second['search_calls'] == 1
    assert len(calls) == 3 and first['errors'] and second['errors']
    with Session(engine) as session:
        assert sum(row.calls for row in session.exec(select(discovery.SearchQuotaUsage)).all()) == 3
        assert not session.exec(select(db.QuotaUsage)).all()


def test_video_requests_respect_general_daily_quota_without_consuming_search_bucket(index):
    engine, _ = index
    QuotaGuard(engine, daily_max_units=1).charge(1)
    calls = []
    def handle(request):
        calls.append(request)
        return search([vid(1)], 'next')
    report = run(engine, handle, daily_max_units=1)
    assert len(calls) == 1 and report['errors'] and report['pages'] == 0
    assert report['search_calls'] == 1 and query_state(engine).page_token is None
    with Session(engine) as session:
        assert sum(row.units for row in session.exec(select(db.QuotaUsage)).all()) == 1
        assert sum(row.calls for row in session.exec(select(discovery.SearchQuotaUsage)).all()) == 1


def test_total_per_run_request_budget_bounds_search_and_details(index):
    engine, _ = index
    calls = []
    def handle(request):
        calls.append(request)
        return search([vid(1)]) if request.url.path.endswith('/search') else details(request)
    report = run(engine, handle, queries=['a', 'b', 'c'], max_units=2)
    assert len(calls) == 2 and report['quota_units'] == 2 and report['pages'] == 1


def test_completed_query_cooldown_survives_database_restart(index):
    engine, clock = index
    calls = []
    def handle(request):
        calls.append(request)
        return search()
    run(engine, handle)
    engine.dispose()
    clock[0] += timedelta(hours=5)
    assert run(engine, handle)['search_calls'] == 0
    clock[0] += timedelta(hours=2)
    assert run(engine, handle)['search_calls'] == 1
    assert len(calls) == 2
    assert datetime.fromisoformat(calls[-1].url.params['publishedAfter']) == clock[0]-timedelta(days=14)


def test_incomplete_page_keeps_pinned_window_across_restart(index):
    engine, clock = index
    requests = []
    def handle(request):
        requests.append(request)
        return search([], 'next')
    run(engine, handle)
    engine.dispose()
    clock[0] += timedelta(hours=1)
    run(engine, handle)
    assert requests[-1].url.params['pageToken'] == 'next'
    assert requests[-1].url.params['publishedAfter'] == requests[0].url.params['publishedAfter']


def test_day_old_backlog_restarts_recent_window(index):
    engine, clock = index
    requests = []
    def handle(request):
        requests.append(request)
        return search([], 'next')
    run(engine, handle)
    clock[0] += timedelta(hours=25)
    run(engine, handle)
    assert 'pageToken' not in requests[-1].url.params
    assert datetime.fromisoformat(requests[-1].url.params['publishedAfter']) == clock[0]-timedelta(days=14)


def test_retention_hides_and_purges_expired_sources_and_cursors(index):
    engine, _ = index
    old = NOW-timedelta(days=31)
    with Session(engine) as session:
        session.add(discovery.DiscoveredSource(channel_id=channel(1), first_seen_at=old, last_seen_at=old))
        session.add(discovery.DiscoveredSource(channel_id=channel(2), first_seen_at=NOW, last_seen_at=NOW))
        session.add(discovery.DiscoveryQuery(query='old', page_token='cached', last_attempted_at=old))
        session.commit()
    assert {row['channel_id'] for row in discovery.merged_watchlist(engine, [])} == {channel(2)}
    discovery.purge_discovery_cache(engine)
    with Session(engine) as session:
        assert session.get(discovery.DiscoveredSource, channel(1)) is None
        assert session.get(discovery.DiscoveryQuery, 'old') is None
        assert session.get(discovery.DiscoveredSource, channel(2)) is not None


def test_offline_extraction_does_not_refresh_detail_or_registry_timestamps(index):
    engine, clock = index
    def handle(request):
        return search([vid(1)]) if request.url.path.endswith('/search') else details(request)
    run(engine, handle)
    with Session(engine) as session:
        original = session.exec(select(db.Video)).one().fetched_at
        seen = session.get(discovery.DiscoveredSource, channel(1)).last_seen_at
    clock[0] += timedelta(hours=7)
    db.update_extraction_sync(engine, vid(1), None)
    with Session(engine) as session:
        assert session.exec(select(db.Video)).one().fetched_at == original
        assert session.get(discovery.DiscoveredSource, channel(1)).last_seen_at == seen
    run(engine, handle)
    with Session(engine) as session:
        assert session.exec(select(db.Video)).one().fetched_at > original
        assert session.get(discovery.DiscoveredSource, channel(1)).last_seen_at > seen


def test_confirmed_missing_video_detail_removes_previously_cached_row(index):
    engine, _ = index
    db.save_giveaway_sync(engine, {'id': vid(1), 'description': 'Previously public video'}, None)
    def handle(request):
        if request.url.path.endswith('/search'):
            return search([vid(1)], 'next')
        return httpx.Response(200, json={'kind': 'youtube#videoListResponse', 'pageInfo': {'totalResults': 0}})
    report = run(engine, handle)
    assert report['pages'] == 1 and report['videos_saved'] == 0
    assert query_state(engine).page_token == 'next'
    with Session(engine) as session:
        assert not session.exec(select(db.Video)).all()


def test_malformed_details_preserve_cached_video_and_cursor(index):
    engine, _ = index
    db.save_giveaway_sync(engine, {'id': vid(1), 'description': 'Retained source evidence'}, None)
    def handle(request):
        if request.url.path.endswith('/search'):
            return search([vid(1)], 'next')
        return httpx.Response(200, json={})
    report = run(engine, handle)
    assert report['errors'] and report['pages'] == 0
    assert query_state(engine).page_token is None
    with Session(engine) as session:
        assert session.exec(select(db.Video)).one().raw_description == 'Retained source evidence'
        assert not session.exec(select(discovery.DiscoveredSource)).all()
