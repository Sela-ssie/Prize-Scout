import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from scripts.configure_watchlist import configure, write_watchlist
from src import db
from src.ingestion import QuotaExceeded, load_watchlist
from src.youtube_client import YouTubeClient, YouTubeRequestError

CHANNEL = 'UC' + 'a' * 22
PLAYLIST = 'UU' + 'a' * 22


@pytest.mark.parametrize('payload', [{}, {'channels': None}, {'channels': [True]},
    {'channels': ['UCshort']}, {'channels': [{'channel_id': CHANNEL, 'uploads_playlist_id': 12}]},
    {'channels': [{'channel_id': CHANNEL, 'uploads_playlist_id': 'PL' + 'a'*22}]},
    {'channels': [{'channel_id': CHANNEL, 'title': None}]}])
def test_invalid_watchlists(tmp_path, payload):
    path = tmp_path / 'watchlist.json'
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_watchlist(path)


def test_watchlist_deduplicates_and_atomic_write(tmp_path):
    path = tmp_path / 'new' / 'watchlist.json'
    row = {'channel_id': CHANNEL, 'uploads_playlist_id': PLAYLIST, 'title': 'A'}
    write_watchlist(path, [row, row])
    assert load_watchlist(path) == [row]
    assert list(path.parent.iterdir()) == [path]


def test_resolution_preserves_existing_and_charges_shared_quota(tmp_path):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path}/index.db')
    existing = {'channel_id': CHANNEL, 'uploads_playlist_id': PLAYLIST, 'title': 'Curated'}
    calls = []
    def handle(request):
        calls.append(request)
        assert request.url.params['forHandle'] == '@example'
        return httpx.Response(200, json={'items': [{'id': CHANNEL,
            'contentDetails': {'relatedPlaylists': {'uploads': PLAYLIST}}, 'snippet': {'title': 'New'}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = YouTubeClient('secret', client=http)
            rows, spent = await configure(engine, ['@example', '@example', CHANNEL], [existing], client=client)
            assert rows == [existing] and spent == 1
            rows, spent = await configure(engine, ['@example'], [existing], client=client, replace_existing=True)
            assert rows[0]['title'] == 'New' and spent == 1
            with pytest.raises(QuotaExceeded):
                await configure(engine, ['@example'], [], client=client, daily_max_units=2)
    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize('item', [
    {'contentDetails': None}, {'contentDetails': {'relatedPlaylists': []}},
    {'contentDetails': {'relatedPlaylists': {'uploads': 12}}},
    {'id': None, 'snippet': {'title': 'A'}, 'contentDetails': {'relatedPlaylists': {'uploads': PLAYLIST}}},
    {'id': CHANNEL, 'snippet': None, 'contentDetails': {'relatedPlaylists': {'uploads': PLAYLIST}}},
])
def test_invalid_channel_metadata_is_sanitized(tmp_path, item):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path}/index.db')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={'items': [item]}))) as http:
            with pytest.raises(YouTubeRequestError) as error:
                await configure(engine, ['@example'], [], client=YouTubeClient('secret', client=http))
            assert 'secret' not in str(error.value)
    asyncio.run(run())


def test_cli_dry_run_requires_no_key_and_writes_nothing(tmp_path):
    import os
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / 'watchlist.json'
    env = {key: value for key, value in os.environ.items() if key != 'YOUTUBE_API_KEY'}
    result = subprocess.run([sys.executable, str(root / 'scripts/configure_watchlist.py'),
        '--channel', '@example', '--out', str(out), '--dry-run'], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['channels'] == ['@example']
    assert not out.exists()
