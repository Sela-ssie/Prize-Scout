"""Read-only index coverage counts, separate from extractor quality evaluation."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, and_, case, cast, func, or_, select

from .categories import CATEGORIES
from .db import Giveaway, IngestionState, QuotaUsage, Video


def _clock(now):
    now = now or datetime.now(timezone.utc)
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def _timestamp(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return _clock(value).isoformat()


def _status(engine, now):
    deadline = func.datetime(Giveaway.deadline) if engine.dialect.name == 'sqlite' else cast(Giveaway.deadline, DateTime)
    clock = func.datetime(now) if engine.dialect.name == 'sqlite' else now
    return case(
        (or_(Giveaway.availability == 'expired', deadline <= clock), 'expired'),
        (deadline > clock, 'active'), else_='unknown')


def _count(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def coverage_summary(engine, now=None):
    """Return public-safe aggregate metadata; never load retained source text.

    All mention and analysis counts describe fresh inputs. Stale inputs contribute
    only a count. Unknown legacy analysis does not imply a negative prediction.
    """
    now = _clock(now)
    fresh = Video.fetched_at >= now - timedelta(days=30)
    analyzed = Video.extraction_is_giveaway.is_not(None)
    status = _status(engine, now)
    mention = Giveaway.id.is_not(None)
    counts = [
        _count(fresh).label('indexed_videos'),
        _count(or_(Video.fetched_at < now - timedelta(days=30), Video.fetched_at.is_(None))).label('stale_videos'),
        _count(and_(fresh, analyzed)).label('analyzed_videos'),
        _count(and_(fresh, ~analyzed)).label('unanalyzed_videos'),
        _count(and_(fresh, mention)).label('detected_mentions'),
        _count(and_(fresh, mention, status == 'active')).label('available_mentions'),
        _count(and_(fresh, mention, status == 'unknown')).label('unknown_availability_mentions'),
        _count(and_(fresh, mention, status == 'expired')).label('expired_mentions'),
    ]
    joined = Video.__table__.outerjoin(Giveaway.__table__, Video.video_id == Giveaway.video_id)
    with engine.connect() as connection:
        totals = dict(connection.execute(select(*counts).select_from(joined)).mappings().one())
        totals['campaigns'] = connection.execute(select(func.count(func.distinct(
            func.coalesce(Giveaway.campaign_key, 'video:' + Video.video_id))))
            .select_from(joined).where(fresh, mention, status != 'expired')).scalar_one()
        source_rows = connection.execute(select(Video.channel_id, *counts,
            func.max(case((fresh, Video.channel_title))).label('channel_title'),
            func.max(case((fresh, Video.fetched_at))).label('last_fetched_at'))
            .select_from(joined).group_by(Video.channel_id)).mappings().all()
        states = {row['channel_id']: row for row in connection.execute(select(
            IngestionState.channel_id, IngestionState.channel_title,
            IngestionState.last_succeeded_at, IngestionState.completed_at)).mappings()}
        category_rows = connection.execute(select(Giveaway.categories_json, func.count())
            .select_from(joined).where(fresh, mention, status != 'expired')
            .group_by(Giveaway.categories_json)).all()
    sources = []
    by_source = {row['channel_id']: row for row in source_rows}
    for channel_id in sorted(set(by_source) | set(states), key=lambda value: value or ''):
        row, state = by_source.get(channel_id, {}), states.get(channel_id, {})
        sources.append(dict(
            channel_id=channel_id or '',
            channel_title=row.get('channel_title') or state.get('channel_title') or channel_id or 'Unknown source',
            fresh_videos=row.get('indexed_videos', 0), stale_videos=row.get('stale_videos', 0),
            analyzed_videos=row.get('analyzed_videos', 0), detected_mentions=row.get('detected_mentions', 0),
            active_mentions=row.get('available_mentions', 0), unknown_mentions=row.get('unknown_availability_mentions', 0),
            expired_mentions=row.get('expired_mentions', 0), last_fetched_at=_timestamp(row.get('last_fetched_at')),
            last_ingested_at=_timestamp(state.get('last_succeeded_at')),
            backfill_complete=state.get('completed_at') is not None))
    category_counts = dict.fromkeys(CATEGORIES, 0)
    for raw, count in category_rows:
        try:
            categories = json.loads(raw or '["other"]')
        except (ValueError, TypeError):
            categories = ['other']
        if not isinstance(categories, list):
            categories = ['other']
        for category in set(item for item in categories if isinstance(item, str)):
            if category in category_counts:
                category_counts[category] += count
    totals['source_count'] = len(sources)
    return dict(generated_at=now.isoformat(), totals=totals, sources=sources,
        categories=[dict(id=key, label=label, count=category_counts[key]) for key, label in CATEGORIES.items()],
        note='Counts describe index coverage, not extraction accuracy. Missing legacy analysis is unknown; availability is not independently verified.')


def diagnostic_report(engine, now=None):
    """Local-only aggregate diagnostics, without descriptions or configuration."""
    now = _clock(now)
    summary = coverage_summary(engine, now)
    fresh = Video.fetched_at >= now - timedelta(days=30)
    day = now.astimezone(ZoneInfo('America/Los_Angeles')).date().isoformat()
    with engine.connect() as connection:
        inputs = dict(connection.execute(select(
            func.count().label('videos'),
            _count(Video.raw_description.is_not(None)).label('with_raw_description'),
            _count(and_(Video.raw_description.is_not(None), Video.raw_description != '')).label('with_nonempty_description'),
            _count(and_(Video.title.is_not(None), Video.title != '')).label('with_title'),
            _count(Video.extraction_is_giveaway.is_(True)).label('positive_predictions'),
            _count(Video.extraction_is_giveaway.is_(False)).label('negative_predictions'),
            _count(Video.extraction_is_giveaway.is_(None)).label('unknown_predictions'),
        ).where(fresh)).mappings().one())
        reasons = connection.execute(select(Video.extraction_reason, func.count())
            .where(fresh, Video.extraction_is_giveaway.is_(False)).group_by(Video.extraction_reason)).all()
        versions = connection.execute(select(Video.extractor_version, func.count())
            .where(fresh).group_by(Video.extractor_version)).all()
        units = connection.execute(select(QuotaUsage.units).where(QuotaUsage.day == day)).scalar_one_or_none()
    reason_counts, version_counts = {'unknown': 0}, {'unknown': 0}
    for reason, count in reasons:
        key = reason or 'unknown'
        reason_counts[key] = reason_counts.get(key, 0) + count
    for version, count in versions:
        key = version or 'unknown'
        version_counts[key] = version_counts.get(key, 0) + count
    return dict(coverage=summary, input_coverage=inputs,
        rejection_reasons=reason_counts, extractor_versions=version_counts,
        quota=dict(day=day, timezone='America/Los_Angeles', units=units or 0),
        freshness=dict(window_days=30, cutoff=(now - timedelta(days=30)).isoformat(),
                       fresh_videos=summary['totals']['indexed_videos'], stale_videos=summary['totals']['stale_videos']))
