"""SQLite index, safe legacy upgrades, and durable ingestion state."""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import DateTime, func, inspect, text, case, and_, or_
from sqlmodel import Field, Session, SQLModel, create_engine, select
from .categories import CATEGORIES, classify_prizes
from .campaigns import campaign_identity, reported_differences


def _utcnow():
    return datetime.now(timezone.utc)


class Video(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: str = Field(unique=True, index=True)
    channel_id: Optional[str] = Field(default=None, index=True)
    channel_title: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[str] = None
    raw_description: Optional[str] = None
    fetched_at: datetime = Field(default_factory=_utcnow, index=True)
    extraction_is_giveaway: Optional[bool] = None
    extraction_confidence: Optional[float] = None
    extraction_reason: Optional[str] = None
    extractor_version: Optional[str] = None
    extracted_at: Optional[datetime] = None


class Giveaway(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: str = Field(unique=True, index=True)
    prizes: Optional[str] = None
    prizes_json: Optional[str] = None
    deadline: Optional[str] = None
    howto: Optional[str] = None
    confidence: Optional[float] = Field(default=None, index=True)
    extractor_version: Optional[str] = None
    availability: str = Field(default='unknown', index=True)
    categories_json: Optional[str] = None
    campaign_key: Optional[str] = Field(default=None, index=True)
    campaign_url: Optional[str] = None
    evidence_json: Optional[str] = None
    updated_at: datetime = Field(default_factory=_utcnow)


class IngestionState(SQLModel, table=True):
    channel_id: str = Field(primary_key=True)
    channel_title: Optional[str] = None
    uploads_playlist_id: Optional[str] = None
    page_token: Optional[str] = None
    completed_at: Optional[datetime] = None
    last_attempted_at: Optional[datetime] = None
    last_succeeded_at: Optional[datetime] = None


class QuotaUsage(SQLModel, table=True):
    day: str = Field(primary_key=True)
    units: int = 0


def create_sync_engine(url):
    options = {'connect_args': {'check_same_thread': False, 'timeout': 30}} if url.startswith('sqlite:') else {}
    return create_engine(url, echo=False, **options)


def create_db_and_tables(sync_url):
    engine = create_sync_engine(sync_url)
    SQLModel.metadata.create_all(engine)
    # Add nullable columns first so old rows survive. Duplicate legacy rows are
    # archived before consolidation, preserving original data for inspection.
    inspector = inspect(engine)
    with engine.begin() as conn:
        if 'last_attempted_at' not in {c['name'] for c in inspector.get_columns('ingestionstate')}:
            conn.execute(text('ALTER TABLE ingestionstate ADD COLUMN last_attempted_at DATETIME'))
        if 'last_succeeded_at' not in {c['name'] for c in inspector.get_columns('ingestionstate')}:
            conn.execute(text('ALTER TABLE ingestionstate ADD COLUMN last_succeeded_at DATETIME'))
        if 'channel_title' not in {c['name'] for c in inspector.get_columns('ingestionstate')}:
            conn.execute(text('ALTER TABLE ingestionstate ADD COLUMN channel_title VARCHAR'))
        needs_fts_build = 'video_fts' not in inspector.get_table_names()
        for model in (Video, Giveaway):
            table = model.__tablename__
            present = {c['name'] for c in inspector.get_columns(table)}
            for col in model.__table__.columns:
                if col.name not in present:
                    kind = col.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {col.name} {kind}'))
            duplicate = conn.execute(text(f'SELECT video_id FROM {table} GROUP BY video_id HAVING COUNT(*) > 1')).first()
            if duplicate:
                conn.execute(text(f'CREATE TABLE IF NOT EXISTS {table}_legacy_archive AS SELECT * FROM {table} WHERE 1=0'))
                conn.execute(text(f'INSERT INTO {table}_legacy_archive SELECT * FROM {table} WHERE id NOT IN (SELECT MAX(id) FROM {table} GROUP BY video_id)'))
                conn.execute(text(f'DELETE FROM {table} WHERE id NOT IN (SELECT MAX(id) FROM {table} GROUP BY video_id)'))
            conn.execute(text(f'CREATE UNIQUE INDEX IF NOT EXISTS ux_{table}_video_id ON {table}(video_id)'))
        conn.execute(text("UPDATE giveaway SET availability='unknown' WHERE availability IS NULL"))
        for row in conn.execute(text('SELECT id, prizes, prizes_json FROM giveaway WHERE categories_json IS NULL')).mappings().all():
            prizes = json.loads(row['prizes_json'] or '[]')
            categories = classify_prizes(prizes, row['prizes'])
            conn.execute(text('UPDATE giveaway SET categories_json=:categories WHERE id=:id'),
                         {'categories': json.dumps(categories), 'id': row['id']})
        # Missing fetch timestamps are deliberately old, requiring refresh/drop.
        conn.execute(text("UPDATE video SET fetched_at='1970-01-01 00:00:00' WHERE fetched_at IS NULL"))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_video_fetched_at ON video(fetched_at)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_giveaway_campaign_key ON giveaway(campaign_key)'))
        if engine.dialect.name == 'sqlite':
            conn.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS video_fts USING fts5(title, raw_description, content='video', content_rowid='id')"))
            conn.execute(text("CREATE TRIGGER IF NOT EXISTS video_ai AFTER INSERT ON video BEGIN INSERT INTO video_fts(rowid,title,raw_description) VALUES(new.id,new.title,new.raw_description); END"))
            conn.execute(text("CREATE TRIGGER IF NOT EXISTS video_ad AFTER DELETE ON video BEGIN INSERT INTO video_fts(video_fts,rowid,title,raw_description) VALUES('delete',old.id,old.title,old.raw_description); END"))
            conn.execute(text("CREATE TRIGGER IF NOT EXISTS video_au AFTER UPDATE ON video BEGIN INSERT INTO video_fts(video_fts,rowid,title,raw_description) VALUES('delete',old.id,old.title,old.raw_description); INSERT INTO video_fts(rowid,title,raw_description) VALUES(new.id,new.title,new.raw_description); END"))
            if needs_fts_build:
                conn.execute(text("INSERT INTO video_fts(video_fts) VALUES('rebuild')"))
    return engine


def get_sync_session(engine):
    return Session(engine)


def save_giveaway_sync(engine, video, extracted=None, *, analysis=None):
    """Persist full input including negatives; remove obsolete positive extraction."""
    if engine is None or not (video_id := video.get('id') or video.get('video_id')):
        return
    with Session(engine) as session:
        v = session.exec(select(Video).where(Video.video_id == video_id)).first()
        if v is None:
            v = Video(video_id=video_id)
        v.channel_id = video.get('channel_id')
        v.channel_title = video.get('channel_title')
        v.title = video.get('title')
        v.url = video.get('url') or f'https://www.youtube.com/watch?v={video_id}'
        v.published_at = video.get('published_at')
        v.raw_description = video.get('description', '')
        v.fetched_at = _utcnow()
        _write_analysis(v, analysis)
        session.add(v)
        _write_extraction(session, video_id, extracted)
        session.commit()


def _write_analysis(video, analysis):
    # Missing legacy analysis is unknown, never silently a negative prediction.
    if analysis is None:
        video.extraction_is_giveaway = None
        video.extraction_confidence = None
        video.extraction_reason = None
        video.extractor_version = None
        video.extracted_at = None
        return
    video.extraction_is_giveaway = analysis['is_giveaway']
    video.extraction_confidence = analysis['confidence']
    video.extraction_reason = analysis.get('rejected_because')
    video.extractor_version = analysis['extractor_version']
    video.extracted_at = _utcnow()


def _write_extraction(session, video_id, extracted):
    g = session.exec(select(Giveaway).where(Giveaway.video_id == video_id)).first()
    if extracted:
        g = g or Giveaway(video_id=video_id)
        for field in ('prizes', 'deadline', 'howto', 'confidence', 'extractor_version'):
            setattr(g, field, extracted.get(field))
        g.prizes_json = json.dumps(extracted.get('prizes_structured', []))
        g.categories_json = json.dumps(classify_prizes(extracted.get('prizes_structured', []), extracted.get('prizes')))
        g.availability = extracted.get('availability', 'unknown')
        video = session.exec(select(Video).where(Video.video_id == video_id)).one()
        g.campaign_key, g.campaign_url = campaign_identity(video.raw_description)
        from .evidence import build_evidence
        g.evidence_json = json.dumps(build_evidence(video.title or '', video.raw_description or '', extracted,
                                                   published_at=video.published_at))
        g.updated_at = _utcnow()
        session.add(g)
    elif g:
        session.delete(g)


def update_extraction_sync(engine, video_id, extracted, *, analysis=None):
    """Offline extraction writes must never renew the source cache timestamp."""
    with Session(engine) as session:
        video = session.exec(select(Video).where(Video.video_id == video_id)).first()
        if video is None:
            return False
        _write_analysis(video, analysis)
        session.add(video)
        _write_extraction(session, video_id, extracted)
        session.commit()
    return True


def purge_legacy_archives(engine):
    """Migration backups are cached API content too; unknown age is expired."""
    tables = set(inspect(engine).get_table_names())
    cutoff = _utcnow() - timedelta(days=30)
    with engine.begin() as conn:
        for table, date_column in [('video_legacy_archive', 'fetched_at'), ('giveaway_legacy_archive', 'updated_at')]:
            if table in tables:
                conn.execute(text(f'DELETE FROM {table} WHERE {date_column} IS NULL OR {date_column} < :cutoff'), {'cutoff': cutoff})


def remove_videos(engine, ids):
    tables = set(inspect(engine).get_table_names())
    with Session(engine) as session:
        for table in ('video_legacy_archive', 'giveaway_legacy_archive'):
            if table in tables:
                for video_id in ids:
                    session.execute(text(f'DELETE FROM {table} WHERE video_id=:video_id'), {'video_id': video_id})
        for model in (Giveaway, Video):
            for row in session.exec(select(model).where(model.video_id.in_(ids))).all():
                session.delete(row)
        session.commit()


def _status_expression(engine):
    # Re-evaluate deadlines on reads: a cached active result can expire between runs.
    expiry = func.datetime(Giveaway.deadline) <= func.datetime('now') if engine.dialect.name == 'sqlite' else func.cast(Giveaway.deadline, DateTime) <= func.now()
    future = func.datetime(Giveaway.deadline) > func.datetime('now') if engine.dialect.name == 'sqlite' else func.cast(Giveaway.deadline, DateTime) > func.now()
    return case((Giveaway.availability == 'expired', 'expired'), (expiry, 'expired'),
                (future, 'active'), else_='unknown')


def index_stats(engine):
    with Session(engine) as session:
        fresh = select(Video).where(Video.fetched_at >= _utcnow() - timedelta(days=30))
        videos = session.exec(select(func.count()).select_from(fresh.subquery())).one()
        giveaways = session.exec(select(func.count()).select_from(Giveaway).join(Video, Video.video_id == Giveaway.video_id)
            .where(Video.fetched_at >= _utcnow() - timedelta(days=30), _status_expression(engine) != 'expired')).one()
        channels = session.exec(select(func.count()).select_from(IngestionState)).one()
        latest = session.exec(select(func.max(IngestionState.last_succeeded_at))).one()
    return dict(indexed_videos=videos, giveaways=giveaways, watched_channels=channels,
                last_ingested_at=latest.replace(tzinfo=timezone.utc) if latest and latest.tzinfo is None else latest,
                demo=os.getenv('PRIZESCOUT_DEMO') == '1')


def category_counts(engine):
    with Session(engine) as session:
        rows = session.exec(select(Giveaway.categories_json, func.count()).join(Video, Video.video_id == Giveaway.video_id)
            .where(Video.fetched_at >= _utcnow() - timedelta(days=30), _status_expression(engine) != 'expired')
            .group_by(Giveaway.categories_json)).all()
    counts = dict.fromkeys(CATEGORIES, 0)
    for raw, count in rows:
        for category in set(json.loads(raw or '["other"]')):
            if category in counts:
                counts[category] += count
    return [dict(id=key, label=label, count=counts[key]) for key, label in CATEGORIES.items()]


def _result(g, v, current_status):
    return dict(id=g.id, video_id=v.video_id, video_title=v.title, video_url=v.url,
        published_at=v.published_at, channel_id=v.channel_id, channel_title=v.channel_title,
        categories=json.loads(g.categories_json or '["other"]'), is_demo=os.getenv('PRIZESCOUT_DEMO') == '1',
        fetched_at=v.fetched_at.replace(tzinfo=timezone.utc) if v.fetched_at.tzinfo is None else v.fetched_at,
        prizes=g.prizes, prizes_structured=json.loads(g.prizes_json or '[]'), deadline=g.deadline,
        howto=g.howto, confidence=g.confidence, extractor_version=g.extractor_version,
        availability=current_status, evidence=json.loads(g.evidence_json or '{}'))


def list_index(engine, q='', page=1, page_size=20, min_confidence=0, availability='all', category='all', sort='confidence', group_campaigns=False):
    status = _status_expression(engine)
    stmt = select(Giveaway, Video, status.label('status')).join(Video, Video.video_id == Giveaway.video_id)
    stmt = stmt.where(Video.fetched_at >= _utcnow() - timedelta(days=30))
    stmt = stmt.where(func.coalesce(Giveaway.confidence, 0) >= min_confidence)
    stmt = stmt.where(status != 'expired') if availability == 'all' else stmt.where(status == availability)
    if category != 'all':
        stmt = stmt.where(Giveaway.categories_json.contains('"' + category + '"'))
    if q.strip():
        words = re.findall(r'\w+', q, flags=re.UNICODE)
        if not words:
            stmt = stmt.where(Video.id == -1)
        elif engine.dialect.name == 'sqlite':
            match = ' AND '.join('"' + w + '"*' for w in words)
            source_match = text('video.id IN (SELECT rowid FROM video_fts WHERE video_fts MATCH :match)')
            prize_match = and_(*(func.lower(Giveaway.prizes).contains(word.lower(), autoescape=True) for word in words))
            stmt = stmt.where(or_(source_match, prize_match)).params(match=match)
        else:
            stmt = stmt.where((Video.title.ilike(f'%{q}%')) | (Video.raw_description.ilike(f'%{q}%')))
    with Session(engine) as session:
        ordering = {
            'confidence': (Giveaway.confidence.desc(), Video.published_at.desc()),
            'newest': (Video.published_at.desc(), Giveaway.confidence.desc()),
            'deadline': (func.datetime(Giveaway.deadline).asc().nullslast(), Giveaway.confidence.desc()),
        }[sort]
        if group_campaigns:
            # Rank matching members before pagination so duplicates never consume
            # page slots. Filtering one member still finds its entire campaign.
            group_key = func.coalesce(Giveaway.campaign_key, 'video:' + Video.video_id)
            ranked = stmt.with_only_columns(Giveaway.id.label('giveaway_id'),
                func.row_number().over(partition_by=group_key, order_by=(*ordering, Video.video_id)).label('position'),
                maintain_column_froms=True).subquery()
            representatives = select(ranked.c.giveaway_id).where(ranked.c.position == 1)
            total = session.exec(select(func.count()).select_from(representatives.subquery())).one()
            page_stmt = select(Giveaway, Video, status.label('status')).join(Video, Video.video_id == Giveaway.video_id).where(Giveaway.id.in_(representatives))
        else:
            total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
            page_stmt = stmt
        rows = session.exec(page_stmt.order_by(*ordering, Video.video_id).offset((page-1)*page_size).limit(page_size)).all()
        keys = {g.campaign_key for g, _, _ in rows if g.campaign_key} if group_campaigns else set()
        members = {}
        if keys:
            related = session.exec(select(Giveaway, Video, status.label('status')).join(Video, Video.video_id == Giveaway.video_id)
                .where(Giveaway.campaign_key.in_(keys), Video.fetched_at >= _utcnow() - timedelta(days=30))
                .order_by(Giveaway.confidence.desc(), Video.video_id)).all()
            for g, v, current_status in related:
                members.setdefault(g.campaign_key, []).append(_result(g, v, current_status))
        results = []
        for g, v, current_status in rows:
            result = _result(g, v, current_status)
            if group_campaigns and g.campaign_key:
                sources = members[g.campaign_key]
                result['campaign'] = dict(id=g.campaign_key, entry_url=g.campaign_url, source_count=len(sources),
                    grouping_reason='shared_entry_url', conflicting_fields=reported_differences(sources), sources=sources)
            results.append(result)
    return dict(query=q, results=results, total=total, page=page, page_size=page_size,
                has_more=page*page_size < total, found=len(results), searched=total,
                group_campaigns=group_campaigns, index_stats=index_stats(engine))
