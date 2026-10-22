import asyncio
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from sqlmodel import Session, select
from src import db
from src.ingestion import QuotaGuard, QuotaExceeded, ingest_once
from src.youtube_client import YouTubeClient, YouTubeRequestError


def engine(tmp_path):
    return db.create_db_and_tables(f'sqlite:///{tmp_path}/ingest.db')


def test_retries_charged_before_network_and_sanitized(tmp_path):
    eng = engine(tmp_path)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(503)
    async def run():
        guard = QuotaGuard(eng, 2, 10)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            yt = YouTubeClient('secret-key', guard=guard, client=http, sleep=lambda _: asyncio.sleep(0))
            with pytest.raises(QuotaExceeded):
                await yt.videos(['v'])
        assert guard.spent == 2
    asyncio.run(run())
    assert len(calls) == 2
    guard = QuotaGuard(eng, 100, 2)
    with pytest.raises(QuotaExceeded):
        guard.charge(1)


def test_no_retry_on_auth_error_does_not_leak_key(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, text='secret-key'))) as http:
            guard = QuotaGuard(engine(tmp_path))
            yt = YouTubeClient('secret-key', guard, http)
            with pytest.raises(YouTubeRequestError) as err:
                await yt.videos(['a'])
            assert 'secret-key' not in str(err.value)
            assert guard.spent == 1
    asyncio.run(run())


def test_ingestion_checkpoints_persists_negatives_refreshes_removed(tmp_path):
    eng = engine(tmp_path)
    db.save_giveaway_sync(eng, {'id': 'deleted', 'title': 'Gone'}, {'prizes': 'Camera'})
    with Session(eng) as session:
        old = session.exec(select(db.Video)).one()
        old.fetched_at = datetime.now(timezone.utc)-timedelta(days=31)
        session.add(old)
        session.commit()
    tokens = []
    def handle(request):
        endpoint = request.url.path.rsplit('/', 1)[-1]
        if endpoint == 'channels':
            return httpx.Response(200, json={'items': [{'contentDetails': {'relatedPlaylists': {'uploads': 'UUabc'}}}]})
        if endpoint == 'playlistItems':
            token = request.url.params.get('pageToken')
            tokens.append(token)
            ids = ['negative'] if token else ['positive']
            return httpx.Response(200, json={'items': [{'contentDetails': {'videoId': i}} for i in ids], **({'nextPageToken': 'second'} if not token else {})})
        ids = request.url.params['id'].split(',')
        rows = []
        for i in ids:
            if i != 'deleted':
                rows.append({'id': i, 'snippet': {'title': 'Camera giveaway' if i == 'positive' else 'A sunny day',
                    'description': 'I am giving away a Canon camera! Subscribe and comment to enter.' if i == 'positive' else 'Walking around town.', 'channelId': 'UCabc'}})
        return httpx.Response(200, json={'items': rows})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            yt = YouTubeClient('key', client=http)
            first = await ingest_once(eng, [{'channel_id': 'UCabc'}], client=yt, max_pages=1)
            assert first['removed'] == 1
            assert first['quota_units'] == 4
            with Session(eng) as session:
                assert session.get(db.IngestionState, 'UCabc').page_token == 'second'
            second = await ingest_once(eng, [{'channel_id': 'UCabc'}], client=yt, max_pages=1)
            assert second['quota_units'] == 4
    asyncio.run(run())
    assert tokens == [None, None, 'second']
    with Session(eng) as session:
        assert {v.video_id for v in session.exec(select(db.Video)).all()} == {'positive', 'negative'}
        assert all(v.raw_description for v in session.exec(select(db.Video)).all())
        assert session.get(db.IngestionState, 'UCabc').page_token is None


def test_quota_failure_does_not_advance_checkpoint_and_drops_stale(tmp_path):
    eng = engine(tmp_path)
    db.save_giveaway_sync(eng, {'id': 'stale', 'description': 'old'}, None)
    with Session(eng) as session:
        v = session.exec(select(db.Video)).one()
        v.fetched_at = datetime.now(timezone.utc)-timedelta(days=31)
        session.add(v)
        session.commit()
    def handle(request):
        return httpx.Response(500)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            report = await ingest_once(eng, [{'channel_id': 'UCabc'}], max_units=1,
                client=YouTubeClient('key', client=http, sleep=lambda _: asyncio.sleep(0)))
            assert report['quota_units'] == 1 and report['removed'] == 1 and report['errors']
    asyncio.run(run())
    with Session(eng) as session:
        assert not session.exec(select(db.Video)).all()
        assert not session.exec(select(db.IngestionState)).all()


def test_partial_page_failure_replays_page_on_restart(tmp_path):
    eng = engine(tmp_path)
    calls = []
    def handle(request):
        calls.append(request.url.path)
        if request.url.path.endswith('playlistItems'):
            return httpx.Response(200, json={'items': [{'contentDetails': {'videoId': 'a'}}], 'nextPageToken': 'next'})
        return httpx.Response(200, json={'items': [{'id': 'a', 'snippet': {'description': 'Ordinary video'}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            yt = YouTubeClient('key', client=http)
            report = await ingest_once(eng, [{'channel_id': 'UCabc', 'uploads_playlist_id': 'UUabc'}], client=yt, max_units=1)
            assert report['pages'] == 0 and report['errors']
            with Session(eng) as session:
                assert session.get(db.IngestionState, 'UCabc').page_token is None
            report = await ingest_once(eng, [{'channel_id': 'UCabc'}], client=yt, max_units=2, max_pages=1)
            assert report['pages'] == 1
            with Session(eng) as session:
                assert session.get(db.IngestionState, 'UCabc').page_token == 'next'
    asyncio.run(run())
    assert len(calls) == 3


def test_channel_title_and_success_timestamp(tmp_path):
    eng = engine(tmp_path)
    def handle(request):
        if request.url.path.endswith('playlistItems'):
            return httpx.Response(200, json={'items': [{'contentDetails': {'videoId': 'a'}}]})
        return httpx.Response(200, json={'items': [{'id': 'a', 'snippet': {
            'title': 'Normal video', 'description': 'A walk', 'channelTitle': 'Creator'}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            report = await ingest_once(eng, [{'channel_id': 'UCabc', 'uploads_playlist_id': 'UUabc'}],
                                       client=YouTubeClient('key', client=http), max_units=2)
            assert not report['errors']
    asyncio.run(run())
    with Session(eng) as session:
        assert session.exec(select(db.Video)).one().channel_title == 'Creator'
        assert session.get(db.IngestionState, 'UCabc').last_succeeded_at is not None


def test_refresh_outage_does_not_block_new_uploads(tmp_path):
    eng = engine(tmp_path)
    db.save_giveaway_sync(eng, {'id': 'old', 'description': 'old'}, None)
    with Session(eng) as session:
        video = session.exec(select(db.Video)).one()
        video.fetched_at = datetime.now(timezone.utc) - timedelta(days=28)
        session.add(video)
        session.commit()
    calls = []
    def handle(request):
        calls.append(request.url.path.rsplit('/', 1)[-1])
        if request.url.path.endswith('videos'):
            return httpx.Response(403)
        return httpx.Response(200, json={'items': []})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            report = await ingest_once(eng, [{'channel_id': 'UCabc', 'uploads_playlist_id': 'UUabc'}],
                                       client=YouTubeClient('key', client=http), max_units=4)
            assert report['pages'] == 1 and len(report['errors']) == 1
    asyncio.run(run())
    assert calls == ['videos', 'playlistItems']


def test_small_budget_advances_parked_backlog(tmp_path):
    eng = engine(tmp_path)
    with Session(eng) as session:
        session.add(db.IngestionState(channel_id='UCabc', uploads_playlist_id='UUabc', page_token='backlog'))
        session.commit()
    tokens = []
    def handle(request):
        if request.url.path.endswith('playlistItems'):
            tokens.append(request.url.params.get('pageToken'))
            return httpx.Response(200, json={'items': [{'contentDetails': {'videoId': 'a'}}], 'nextPageToken': 'later'})
        return httpx.Response(200, json={'items': [{'id': 'a', 'snippet': {'description': 'Ordinary'}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            report = await ingest_once(eng, [{'channel_id': 'UCabc'}],
                                       client=YouTubeClient('key', client=http), max_units=2, max_pages=1)
            assert report['pages'] == 1 and not report['errors']
    asyncio.run(run())
    assert tokens == ['backlog']
    with Session(eng) as session:
        assert session.get(db.IngestionState, 'UCabc').page_token == 'later'


@pytest.mark.parametrize('max_units', [2, 3])
def test_small_run_budget_rotates_channels_without_starving_next(tmp_path, max_units):
    eng = engine(tmp_path)
    playlists = []
    def handle(request):
        if request.url.path.endswith('playlistItems'):
            playlists.append(request.url.params['playlistId'])
            return httpx.Response(200, json={'items': [{'contentDetails': {'videoId': 'a'}}]})
        return httpx.Response(200, json={'items': [{'id': 'a', 'snippet': {}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            for _ in range(2):
                await ingest_once(eng, [{'channel_id': 'UCfirst', 'uploads_playlist_id': 'UUfirst'},
                    {'channel_id': 'UCsecond', 'uploads_playlist_id': 'UUsecond'}],
                    client=YouTubeClient('key', client=http), max_units=max_units, max_pages=1)
    asyncio.run(run())
    assert playlists == ['UUfirst', 'UUsecond']
