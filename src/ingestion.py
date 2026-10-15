"""Watchlist ingestion with durable page checkpoints and daily quota accounting."""
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import text
from sqlmodel import Session, select
from . import db
from .extractor import extract, giveaway_payload
from .youtube_client import YouTubeClient, YouTubeRequestError


class QuotaExceeded(RuntimeError):
    pass


class QuotaGuard:
    def __init__(self, engine, max_units=200, daily_max_units=2000):
        if max_units < 1 or daily_max_units < 1:
            raise ValueError('Quota limits must be positive')
        self.engine = engine
        self.max_units = max_units
        self.daily_max_units = daily_max_units
        self.spent = 0

    def charge(self, cost):
        if self.spent + cost > self.max_units:
            raise QuotaExceeded('Per-run quota cap reached')
        day = datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat()
        with self.engine.begin() as conn:
            # Atomic upsert and conditional increment serialize concurrent workers.
            conn.execute(text('INSERT INTO quotausage(day, units) VALUES (:day, 0) ON CONFLICT(day) DO NOTHING'), {'day': day})
            result = conn.execute(text('UPDATE quotausage SET units=units+:cost WHERE day=:day AND units+:cost<=:cap'),
                                  {'day': day, 'cost': cost, 'cap': self.daily_max_units})
            if result.rowcount != 1:
                raise QuotaExceeded('Daily ingestion quota cap reached')
        self.spent += cost


def load_watchlist(path):
    with open(path) as file:
        data = json.load(file)
    rows = data.get('channels') if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError('Watchlist must be a list or an object with a channels list')
    out = []
    seen = set()
    for row in rows:
        row = {'channel_id': row} if isinstance(row, str) else row
        if not isinstance(row, dict) or not isinstance(row.get('channel_id'), str) or not re.fullmatch(r'UC[A-Za-z0-9_-]{22}', row['channel_id']):
            raise ValueError('Each watchlist entry requires a YouTube UC channel_id')
        playlist = row.get('uploads_playlist_id')
        if playlist is not None and (not isinstance(playlist, str) or not re.fullmatch(r'UU[A-Za-z0-9_-]{22}', playlist)):
            raise ValueError('uploads_playlist_id must be a full YouTube UU playlist ID')
        if 'title' in row and not isinstance(row['title'], str):
            raise ValueError('Channel title must be text')
        if row['channel_id'] not in seen:
            out.append(row)
            seen.add(row['channel_id'])
    return out


def stale_ids(engine, days=27):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with Session(engine) as session:
        return list(session.exec(select(db.Video.video_id).where(db.Video.fetched_at < cutoff).order_by(db.Video.fetched_at)).all())


def persist_rows(engine, rows):
    for video in rows:
        analysis = extract(video.get('description', ''),
                        title=video.get('title'), published_at=video.get('published_at'))
        db.save_giveaway_sync(engine, video, giveaway_payload(analysis), analysis=analysis)


async def ingest_once(engine, watchlist, *, max_units=200, daily_max_units=2000, max_pages=2, client=None):
    if max_pages < 1:
        raise ValueError('max_pages must be positive')
    guard = QuotaGuard(engine, max_units, daily_max_units)
    client = client or YouTubeClient(guard=guard)
    # Injecting a mock still exercises the guard when the mock uses _request.
    client.guard = guard
    report = {'videos_saved': 0, 'removed': 0, 'pages': 0, 'quota_units': 0, 'errors': []}
    try:
        # Refresh before walking playlists, including negatives. Missing details
        # mean the video was deleted/private and must leave our cache.
        stale = stale_ids(engine)
        refresh_budget = max(1, max_units // 2)
        for offset in range(0, len(stale), 50):
            if guard.spent >= refresh_budget:
                break
            ids = stale[offset:offset+50]
            try:
                rows = await client.videos(ids)
            except YouTubeRequestError as exc:
                report['errors'].append(str(exc))
                continue
            persist_rows(engine, rows)
            absent = set(ids) - {r['id'] for r in rows}
            db.remove_videos(engine, list(absent))
            report['videos_saved'] += len(rows)
            report['removed'] += len(absent)
        with Session(engine) as session:
            attempted = {s.channel_id: s.last_attempted_at for s in session.exec(select(db.IngestionState)).all()}
        # Persisted least-recently-attempted ordering prevents a long channel
        # near the front of a watchlist from taking every pass's quota.
        watchlist = sorted(watchlist, key=lambda c: str(attempted.get(c['channel_id']) or ''))
        for channel in watchlist:
            # A detail-bearing page needs two requests. Do not mark the next
            # channel attempted after the previous one consumed the budget;
            # otherwise the first channel can win the ordering on every pass.
            if guard.spent and guard.max_units - guard.spent < 2:
                raise QuotaExceeded('Per-run quota cap reached')
            try:
                with Session(engine) as session:
                    state = session.get(db.IngestionState, channel['channel_id'])
                    if state is None:
                        state = db.IngestionState(channel_id=channel['channel_id'], uploads_playlist_id=channel.get('uploads_playlist_id'))
                    if channel.get('title'):
                        state.channel_title = channel['title']
                    playlist = state.uploads_playlist_id
                    token = state.page_token
                    state.last_attempted_at = datetime.now(timezone.utc)
                    session.add(state)
                    session.commit()
                    session.refresh(state)
                if not playlist:
                    playlist = await client.uploads_playlist(channel['channel_id'])
                with Session(engine) as session:
                    state.uploads_playlist_id = playlist
                    session.add(state)
                    session.commit()
                if token and guard.max_units - guard.spent >= 4:
                    # Always check the upload head while historical traversal is
                    # parked. This must not replace the durable backlog cursor.
                    head_ids, _ = await client.playlist_page(playlist)
                    head_rows = await client.videos(head_ids)
                    persist_rows(engine, head_rows)
                    missing = set(head_ids) - {r['id'] for r in head_rows}
                    db.remove_videos(engine, list(missing))
                    report['videos_saved'] += len(head_rows)
                    report['removed'] += len(missing)
                    report['pages'] += 1
                    with Session(engine) as session:
                        state = session.get(db.IngestionState, channel['channel_id'])
                        state.last_succeeded_at = datetime.now(timezone.utc)
                        session.add(state)
                        session.commit()
                for _ in range(max_pages):
                    ids, next_token = await client.playlist_page(playlist, token)
                    rows = await client.videos(ids)
                    persist_rows(engine, rows)
                    absent = set(ids) - {r['id'] for r in rows}
                    db.remove_videos(engine, list(absent))
                    report['removed'] += len(absent)
                    # Advance only after every row is committed. A crash replays
                    # this page idempotently instead of skipping partial writes.
                    with Session(engine) as session:
                        state = session.get(db.IngestionState, channel['channel_id'])
                        state.page_token = next_token
                        state.last_succeeded_at = datetime.now(timezone.utc)
                        if not next_token:
                            state.completed_at = datetime.now(timezone.utc)
                        session.add(state)
                        session.commit()
                    report['videos_saved'] += len(rows)
                    report['pages'] += 1
                    token = next_token
                    if not token:
                        break
            except YouTubeRequestError as exc:
                if exc.invalid_page_token:
                    # Uploads can change while a checkpoint is parked. Restart
                    # next pass, retaining every already indexed row.
                    with Session(engine) as session:
                        state = session.get(db.IngestionState, channel['channel_id'])
                        if state:
                            state.page_token = None
                            session.add(state)
                            session.commit()
                report['errors'].append(str(exc))
    except (QuotaExceeded, YouTubeRequestError) as exc:
        report['errors'].append(str(exc))
    finally:
        # API caches older than 30 days are removed even on outage/quota exhaustion.
        db.purge_legacy_archives(engine)
        expired = stale_ids(engine, days=30)
        db.remove_videos(engine, expired)
        report['removed'] += len(expired)
        report['quota_units'] = guard.spent
    return report
