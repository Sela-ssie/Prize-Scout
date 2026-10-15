import asyncio
import json
import threading

from fastapi import FastAPI
import httpx
import pytest

from src import api, db, worker


@pytest.fixture
def enabled(tmp_path, monkeypatch):
    path = tmp_path / 'watchlist.json'
    path.write_text(json.dumps({'channels': ['UC' + 'a' * 22]}))
    monkeypatch.setenv('WATCHLIST_PATH', str(path))
    monkeypatch.setenv('YOUTUBE_API_KEY', 'secret-key')
    monkeypatch.setenv('INGESTION_ENABLED', '1')
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path}/worker.db')
    monkeypatch.setattr(api, 'get_engine', lambda: engine)
    for name in ('INGESTION_INTERVAL', 'INGESTION_MAX_UNITS', 'INGESTION_DAILY_MAX_UNITS', 'INGESTION_MAX_PAGES', 'INGESTION_TIMEOUT'):
        monkeypatch.delenv(name, raising=False)
    return engine


def test_disabled_does_no_work(monkeypatch):
    monkeypatch.delenv('INGESTION_ENABLED', raising=False)
    def fail():
        pytest.fail('disabled worker must not open database')
    monkeypatch.setattr(api, 'get_engine', fail)
    async def run():
        async with worker.lifespan(FastAPI()):
            pass
        monkeypatch.setenv('INGESTION_ENABLED', '0')
        async with worker.lifespan(FastAPI()):
            pass
    asyncio.run(run())


@pytest.mark.parametrize('name,value,match', [
    ('YOUTUBE_API_KEY', '', 'YOUTUBE_API_KEY'),
    ('INGESTION_ENABLED', 'yes', 'INGESTION_ENABLED'),
    ('INGESTION_INTERVAL', '0', 'INGESTION_INTERVAL'),
    ('INGESTION_TIMEOUT', '0', 'INGESTION_TIMEOUT'),
    ('INGESTION_MAX_UNITS', '-1', 'INGESTION_MAX_UNITS'),
    ('INGESTION_DAILY_MAX_UNITS', 'bad', 'INGESTION_DAILY_MAX_UNITS'),
    ('INGESTION_MAX_PAGES', '0', 'INGESTION_MAX_PAGES'),
    ('WATCHLIST_PATH', '/does/not/exist', 'WATCHLIST_PATH'),
])
def test_invalid_configuration(enabled, monkeypatch, name, value, match):
    monkeypatch.setenv(name, value)
    async def run():
        with pytest.raises(RuntimeError, match=match):
            async with worker.lifespan(FastAPI()):
                pytest.fail('startup should fail')
    asyncio.run(run())


def test_empty_watchlist(enabled, monkeypatch, tmp_path):
    path = tmp_path / 'empty.json'
    path.write_text('{"channels": []}')
    monkeypatch.setenv('WATCHLIST_PATH', str(path))
    async def run():
        with pytest.raises(RuntimeError, match='nonempty'):
            async with worker.lifespan(FastAPI()):
                pass
    asyncio.run(run())


def test_pass_offloads_sync_work_and_shutdown_waits(enabled, monkeypatch):
    started, finish, finished = threading.Event(), threading.Event(), threading.Event()
    seen = []
    async def ingest(engine, watchlist, **options):
        seen.append((engine, watchlist, options))
        started.set()
        assert finish.wait(3), 'test did not release worker'
        finished.set()
        return {'videos_saved': 2, 'pages': 1, 'quota_units': 2, 'removed': 0, 'errors': []}
    monkeypatch.setattr(worker, 'ingest_once', ingest)
    app = FastAPI(lifespan=worker.lifespan)
    @app.get('/ping')
    async def ping():
        return {'ok': True}
    async def run():
        context = worker.lifespan(app)
        await context.__aenter__()
        assert await asyncio.to_thread(started.wait, 2)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await asyncio.wait_for(client.get('/ping'), timeout=0.5)
                assert response.json() == {'ok': True}
            shutdown = asyncio.create_task(context.__aexit__(None, None, None))
            await asyncio.sleep(0.01)
            assert not shutdown.done()
            finish.set()
            await asyncio.wait_for(shutdown, timeout=2)
        finally:
            finish.set()
        assert finished.is_set()
        assert app.state.ingestion_last_report['videos_saved'] == 2
        assert not app.state.ingestion_enabled
        assert len(seen) == 1
        assert seen[0][2] == {'max_units': 200, 'daily_max_units': 2000, 'max_pages': 2}
    asyncio.run(run())


def test_lock_conflict_and_release(enabled, monkeypatch):
    async def ingest(*args, **kwargs):
        return {}
    monkeypatch.setattr(worker, 'ingest_once', ingest)
    async def run():
        async with worker.lifespan(FastAPI()):
            with pytest.raises(RuntimeError, match='Another ingestion worker'):
                async with worker.lifespan(FastAPI()):
                    pass
        async with worker.lifespan(FastAPI()):
            pass
    asyncio.run(run())


def test_failure_is_sanitized_and_retries(enabled, monkeypatch, caplog):
    attempts = []
    monkeypatch.setenv('INGESTION_INTERVAL', '1')
    async def ingest(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError('secret-key https://example.com?key=secret-key')
        return {'errors': ['secret-key']}
    monkeypatch.setattr(worker, 'ingest_once', ingest)
    app = FastAPI()
    async def run():
        async with worker.lifespan(app):
            for _ in range(150):
                if len(attempts) >= 2 and app.state.ingestion_last_report == {'videos_saved': 0,
                        'removed': 0, 'pages': 0, 'quota_units': 0, 'error_count': 1}:
                    break
                await asyncio.sleep(0.01)
            assert len(attempts) == 2
    asyncio.run(run())
    assert 'secret-key' not in caplog.text
    assert 'ValueError' in caplog.text


def test_timeout_cleans_up_under_lock_then_retries(enabled, monkeypatch, caplog):
    monkeypatch.setenv('INGESTION_TIMEOUT', '1')
    monkeypatch.setenv('INGESTION_INTERVAL', '1')
    attempts = []
    cleaned = threading.Event()
    retried = threading.Event()
    async def ingest(engine, *args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            try:
                await asyncio.sleep(60)
            finally:
                # Cancellation must run cleanup while the worker still owns
                # the lock; a timeout must never leave an unowned live pass.
                with pytest.raises(RuntimeError, match='Another ingestion worker'):
                    with worker.ingestion_lock(engine):
                        pass
                await asyncio.sleep(0.01)
                cleaned.set()
        assert cleaned.is_set()
        retried.set()
        return {}
    monkeypatch.setattr(worker, 'ingest_once', ingest)
    async def run():
        async with worker.lifespan(FastAPI()):
            assert await asyncio.to_thread(retried.wait, 4)
            assert len(attempts) == 2
        with worker.ingestion_lock(enabled):
            pass
    asyncio.run(run())
    assert 'TimeoutError' in caplog.text
    assert 'secret-key' not in caplog.text


def test_standalone_cli_obeys_worker_lock(enabled, monkeypatch, tmp_path, capsys):
    from scripts import ingest
    monkeypatch.setenv('DATABASE_URL', str(enabled.url))
    monkeypatch.setattr('sys.argv', ['ingest.py', '--watchlist', __import__('os').environ['WATCHLIST_PATH']])
    with worker.ingestion_lock(enabled):
        assert ingest.main() == 2
    assert 'Another ingestion worker' in capsys.readouterr().err
