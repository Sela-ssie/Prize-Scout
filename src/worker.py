"""Optional quota-bounded ingestion worker for a single persistent SQLite service."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
import fcntl
import json
import logging
import os
from pathlib import Path

from .ingestion import ingest_once, load_watchlist

logger = logging.getLogger(__name__)


def _positive_env(name, default):
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        raise RuntimeError(f'{name} must be a positive integer') from None
    if value < 1:
        raise RuntimeError(f'{name} must be a positive integer')
    return value


def _configuration():
    if not os.getenv('YOUTUBE_API_KEY', '').strip():
        raise RuntimeError('Ingestion requires YOUTUBE_API_KEY')
    options = {name: _positive_env(env, default) for name, env, default in (
        ('max_units', 'INGESTION_MAX_UNITS', 200),
        ('daily_max_units', 'INGESTION_DAILY_MAX_UNITS', 2000),
        ('max_pages', 'INGESTION_MAX_PAGES', 2))}
    interval = _positive_env('INGESTION_INTERVAL', 3600)
    timeout = _positive_env('INGESTION_TIMEOUT', 120)
    if os.getenv('DISCOVERY_ENABLED', '1') not in ('0', '1'):
        raise RuntimeError('DISCOVERY_ENABLED must be 0 or 1')
    if os.getenv('DISCOVERY_ENABLED', '1') == '1':
        for name, default in (('DISCOVERY_MAX_UNITS', 32), ('DISCOVERY_MAX_SEARCH_CALLS', 8),
                              ('DISCOVERY_DAILY_SEARCH_CALLS', 80), ('DISCOVERY_LOOKBACK_DAYS', 14)):
            _positive_env(name, default)
        from .discovery import load_queries
        try:
            load_queries(os.getenv('DISCOVERY_QUERIES_PATH'))
        except (OSError, ValueError):
            raise RuntimeError('Discovery requires a valid query configuration') from None
    try:
        watchlist = load_watchlist(os.getenv('WATCHLIST_PATH', 'config/watchlist.json'))
    except (OSError, ValueError):
        raise RuntimeError('Ingestion requires a valid WATCHLIST_PATH file') from None
    if not watchlist and os.getenv('DISCOVERY_ENABLED', '1') == '0':
        raise RuntimeError('Ingestion requires a nonempty watchlist')
    return watchlist, interval, timeout, options


def _lock(engine):
    database = engine.url.database
    if engine.dialect.name != 'sqlite' or not database or database == ':memory:':
        raise RuntimeError('Ingestion requires a persistent SQLite database')
    try:
        handle = open(Path(database).resolve().with_name(Path(database).name + '.ingestion.lock'), 'a')
    except OSError:
        raise RuntimeError('Unable to open the ingestion worker lock') from None
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('Another ingestion worker holds this database lock') from None
    return handle


@contextmanager
def ingestion_lock(engine):
    """Serialize embedded and standalone ingestion for a persistent database."""
    handle = _lock(engine)
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


async def _bounded_pass(engine, watchlist, options, timeout):
    # Enforce the deadline in the thread's own event loop so cancellation
    # completes ingestion's cleanup before the thread and lock can finish.
    return await asyncio.wait_for(pipeline_once(engine, watchlist, **options), timeout=timeout)


async def pipeline_once(engine, watchlist, *, max_units=200, daily_max_units=2000, max_pages=2):
    """Reserve a bounded discovery share, then poll curated and discovered sources."""
    from .discovery import discover_once, load_queries, merged_watchlist, purge_discovery_cache
    discovery = dict(quota_units=0, search_calls=0, videos_saved=0, sources_added=0, errors=[])
    if os.getenv('DISCOVERY_ENABLED', '1') == '1' and max_units >= 4:
        budget = min(_positive_env('DISCOVERY_MAX_UNITS', 32), max_units//3)
        discovery = await discover_once(engine, load_queries(os.getenv('DISCOVERY_QUERIES_PATH')),
            max_units=max(2, budget), daily_max_units=daily_max_units,
            max_search_calls=_positive_env('DISCOVERY_MAX_SEARCH_CALLS', 8),
            daily_max_search_calls=_positive_env('DISCOVERY_DAILY_SEARCH_CALLS', 80),
            lookback_days=_positive_env('DISCOVERY_LOOKBACK_DAYS', 14))
    purge_discovery_cache(engine)
    report = await ingest_once(engine, merged_watchlist(engine, watchlist),
        max_units=max_units-discovery['quota_units'], daily_max_units=daily_max_units, max_pages=max_pages)
    for name in ('quota_units', 'videos_saved'):
        report[name] = report.get(name, 0)+discovery[name]
    report['search_calls'], report['sources_added'] = discovery['search_calls'], discovery['sources_added']
    report['errors'] = discovery['errors']+report.get('errors', [])
    return report


async def _run(engine, watchlist, interval, timeout, options, stop, state):
    while not stop.is_set():
        state.ingestion_running = True
        try:
            # Extraction, SQLite writes and date parsing execute off the HTTP
            # event loop. Each pass owns its own async network event loop.
            report = await asyncio.to_thread(
                lambda: asyncio.run(_bounded_pass(engine, watchlist, options, timeout)))
            summary = {key: report.get(key, 0) for key in
                       ('videos_saved', 'removed', 'pages', 'quota_units')}
            summary['error_count'] = len(report.get('errors', []))
            state.ingestion_last_report = summary
            logger.info('Ingestion pass %s', json.dumps(summary, sort_keys=True))
        except Exception as exc:
            # Exception text can contain request URLs, SQL, or credentials.
            state.ingestion_last_report = {'error_count': 1}
            logger.error('Ingestion pass failed (%s)', type(exc).__name__)
        finally:
            state.ingestion_running = False
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app):
    enabled = os.getenv('INGESTION_ENABLED', '0')
    if enabled == '0':
        yield
        return
    if enabled != '1':
        raise RuntimeError('INGESTION_ENABLED must be 0 or 1')
    watchlist, interval, timeout, options = _configuration()
    # Deferred import keeps api -> main -> worker imports acyclic.
    from .api import get_engine
    engine = get_engine()
    with ingestion_lock(engine):
        app.state.ingestion_enabled = True
        app.state.ingestion_running = False
        app.state.ingestion_last_report = None
        stop = asyncio.Event()
        task = asyncio.create_task(_run(engine, watchlist, interval, timeout, options, stop, app.state))
        try:
            yield
        finally:
            stop.set()
            try:
                # Do not cancel a to_thread pass: its thread would keep running
                # after the lock was released. Await its bounded completion.
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            finally:
                app.state.ingestion_enabled = False
