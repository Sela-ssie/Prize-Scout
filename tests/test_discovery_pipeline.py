"""Discovery and source polling share one bounded pass without a source ceiling."""
import asyncio
import json

from src import db, discovery, worker
from scripts import discover


def test_discovery_sources_join_same_pass_with_remaining_budget(tmp_path, monkeypatch):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path}/pipeline.db')
    monkeypatch.setenv('DISCOVERY_ENABLED', '1')
    monkeypatch.setenv('DISCOVERY_MAX_UNITS', '32')
    seen = []
    async def search(engine, queries, **options):
        assert options['max_units'] == 4
        discovery.remember_sources(engine, [dict(channel_id='UC'+str(i).zfill(22), channel_title='Source') for i in range(14)])
        return dict(quota_units=3, search_calls=1, videos_saved=2, sources_added=14, errors=[])
    async def ingest(engine, watchlist, **options):
        seen.extend(watchlist)
        assert options['max_units'] == 9
        return dict(quota_units=9, videos_saved=20, pages=2, errors=[])
    monkeypatch.setattr(discovery, 'discover_once', search)
    monkeypatch.setattr(worker, 'ingest_once', ingest)
    try:
        report = asyncio.run(worker.pipeline_once(engine, [dict(channel_id='UC'+'z'*22)], max_units=12))
        assert len(seen) == 15
        assert report['quota_units'] == 12 and report['videos_saved'] == 22
        assert report['sources_added'] == 14 and report['search_calls'] == 1
    finally:
        engine.dispose()


def test_tiny_budget_still_polls_discovered_sources_without_starting_search(tmp_path, monkeypatch):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path}/tiny.db')
    monkeypatch.setenv('DISCOVERY_ENABLED', '1')
    discovery.remember_sources(engine, [dict(channel_id='UC'+'a'*22, channel_title='Discovered')])
    async def fail(*args, **options):
        raise AssertionError('must reserve enough requests for a complete discovery page')
    async def ingest(engine, watchlist, **options):
        assert len(watchlist) == 1 and options['max_units'] == 3
        return dict(quota_units=3, videos_saved=1, errors=[])
    monkeypatch.setattr(discovery, 'discover_once', fail)
    monkeypatch.setattr(worker, 'ingest_once', ingest)
    try:
        assert asyncio.run(worker.pipeline_once(engine, [], max_units=3))['quota_units'] == 3
    finally:
        engine.dispose()


def test_discovery_dry_run_needs_no_key_or_database(tmp_path, monkeypatch, capsys):
    path = tmp_path/'absent.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{path}')
    monkeypatch.delenv('YOUTUBE_API_KEY', raising=False)
    assert discover.main(['--dry-run']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['queries'] >= 14 and report['channel_count_limit'] is None
    assert not path.exists()
