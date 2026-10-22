"""Quota-bounded discovery across YouTube, independent of the starter watchlist."""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlmodel import Field, SQLModel, Session, select


class DiscoveryQuery(SQLModel, table=True):
    query: str = Field(primary_key=True)
    page_token: Optional[str] = None
    published_after: Optional[str] = None
    window_started_at: Optional[datetime] = None
    last_attempted_at: Optional[datetime] = None
    last_succeeded_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class DiscoveredSource(SQLModel, table=True):
    channel_id: str = Field(primary_key=True)
    channel_title: str = ''
    first_seen_at: datetime
    last_seen_at: datetime = Field(index=True)


class SearchQuotaUsage(SQLModel, table=True):
    day: str = Field(primary_key=True)
    calls: int = 0


def _now():
    return datetime.now(timezone.utc)


def load_queries(path=None):
    path = Path(path) if path else Path(__file__).resolve().parents[1] / 'config/discovery.json'
    data = json.loads(path.read_text())
    queries = data.get('queries') if isinstance(data, dict) else data
    if not isinstance(queries, list) or not queries:
        raise ValueError('Discovery requires a nonempty query list')
    if any(not isinstance(query, str) or not query.strip() or len(query) > 200 for query in queries):
        raise ValueError('Discovery queries must be text between 1 and 200 characters')
    return list(dict.fromkeys(query.strip() for query in queries))


def remember_sources(engine, rows):
    """Only successful detail fetches refresh API-derived source metadata."""
    from .db import IngestionState
    now = _now()
    sources = {row.get('channel_id'): row.get('channel_title', '') for row in rows
               if re.fullmatch(r'UC[A-Za-z0-9_-]{22}', row.get('channel_id') or '')}
    added = 0
    with Session(engine) as session:
        for channel_id, title in sources.items():
            source = session.get(DiscoveredSource, channel_id)
            if source is None:
                source = DiscoveredSource(channel_id=channel_id, first_seen_at=now, last_seen_at=now)
                added += 1
            source.channel_title, source.last_seen_at = title, now
            session.add(source)
            state = session.get(IngestionState, channel_id)
            if state is None:
                session.add(IngestionState(channel_id=channel_id, channel_title=title))
        session.commit()
    return added


def merged_watchlist(engine, curated):
    """No channel-count ceiling; retain curated choices and fresh discovered sources."""
    rows = {row['channel_id']: dict(row) for row in curated}
    with Session(engine) as session:
        for source in session.exec(select(DiscoveredSource).where(
                DiscoveredSource.last_seen_at >= _now()-timedelta(days=30))).all():
            rows.setdefault(source.channel_id, dict(channel_id=source.channel_id, title=source.channel_title))
    return list(rows.values())


def purge_discovery_cache(engine):
    """Query cursors and discovered metadata obey the same maximum cache lifetime."""
    cutoff = _now()-timedelta(days=30)
    from .db import IngestionState, Video
    with Session(engine) as session:
        for source in session.exec(select(DiscoveredSource).where(DiscoveredSource.last_seen_at < cutoff)).all():
            retained = session.exec(select(Video.id).where(Video.channel_id == source.channel_id,
                Video.fetched_at >= cutoff).limit(1)).first()
            state = session.get(IngestionState, source.channel_id)
            if state and retained is None:
                session.delete(state)
            session.delete(source)
        for state in session.exec(select(DiscoveryQuery).where(DiscoveryQuery.last_attempted_at < cutoff)).all():
            session.delete(state)
        session.commit()


async def discover_once(engine, queries=None, *, max_units=32, daily_max_units=2000,
                        max_search_calls=8, daily_max_search_calls=80, lookback_days=14,
                        cooldown_hours=6, client=None):
    from . import db
    from .ingestion import QuotaGuard, QuotaExceeded, persist_rows
    from .youtube_client import YouTubeClient, YouTubeRequestError
    if not 1 <= lookback_days <= 365 or cooldown_hours < 0:
        raise ValueError('Invalid discovery lookback or cooldown')
    queries = list(dict.fromkeys(queries if queries is not None else load_queries()))
    if any(not isinstance(query, str) or not query.strip() or len(query) > 200 for query in queries):
        raise ValueError('Invalid discovery query')
    guard = QuotaGuard(engine, max_units, daily_max_units,
                       max_search_calls=max_search_calls, daily_max_search_calls=daily_max_search_calls)
    client = client or YouTubeClient(guard=guard)
    client.guard = guard
    report = dict(search_calls=0, quota_units=0, videos_saved=0, sources_added=0, pages=0, errors=[])
    try:
        with Session(engine) as session:
            attempted = {state.query: state.last_attempted_at for state in session.exec(select(DiscoveryQuery)).all()}
        queries.sort(key=lambda query: str(attempted.get(query) or ''))
        for query in queries:
            if guard.search_spent >= max_search_calls or guard.max_units-guard.spent < 2:
                break
            now = _now()
            with Session(engine) as session:
                state = session.get(DiscoveryQuery, query) or DiscoveryQuery(query=query)
                completed = state.completed_at
                if completed and (completed.replace(tzinfo=timezone.utc) if completed.tzinfo is None else completed) > now-timedelta(hours=cooldown_hours):
                    continue
                started = state.window_started_at
                started = started.replace(tzinfo=timezone.utc) if started and started.tzinfo is None else started
                if not state.published_after or completed or not started or started <= now-timedelta(hours=24):
                    state.published_after = (now-timedelta(days=lookback_days)).isoformat()
                    state.window_started_at = now
                    state.page_token, state.completed_at = None, None
                state.last_attempted_at = now
                token, after = state.page_token, state.published_after
                session.add(state)
                session.commit()
            try:
                ids, next_token = await client.search_videos(query, published_after=after, page_token=token)
                rows = await client.videos(ids)
                report['sources_added'] += persist_rows(engine, rows)
                # A confirmed missing detail must also leave the local index.
                db.remove_videos(engine, list(set(ids)-{row['id'] for row in rows}))
                with Session(engine) as session:
                    state = session.get(DiscoveryQuery, query)
                    state.page_token, state.last_succeeded_at = next_token, _now()
                    if not next_token:
                        state.completed_at = _now()
                    session.add(state)
                    session.commit()
                report['videos_saved'] += len(rows)
                report['pages'] += 1
            except YouTubeRequestError as exc:
                report['errors'].append(str(exc))
                if exc.invalid_page_token:
                    with Session(engine) as session:
                        state = session.get(DiscoveryQuery, query)
                        state.page_token = None
                        session.add(state)
                        session.commit()
    except QuotaExceeded as exc:
        report['errors'].append(str(exc))
    finally:
        purge_discovery_cache(engine)
        db.purge_legacy_archives(engine)
        from .ingestion import stale_ids
        db.remove_videos(engine, stale_ids(engine, days=30))
        report['search_calls'], report['quota_units'] = guard.search_spent, guard.spent
    return report
