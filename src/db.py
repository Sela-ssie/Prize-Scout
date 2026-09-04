import json
import os
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select

DATABASE_URL = os.environ.get("DATABASE_URL")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Video(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: str = Field(unique=True, index=True)
    channel_id: Optional[str] = Field(default=None, index=True)
    title: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[str] = None
    # Never discard the input. Without the raw text every extractor change costs API
    # quota to re-evaluate, which is the single most expensive omission in the prototype.
    raw_description: Optional[str] = None
    # YouTube's terms require cached API data to be refreshed or dropped on a ~30-day
    # cycle; this is the column that makes stale rows findable.
    fetched_at: datetime = Field(default_factory=_utcnow)


class Giveaway(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: str = Field(unique=True, index=True)
    prizes: Optional[str] = None            # human-readable summary, for display
    prizes_json: Optional[str] = None       # structured [{item, value_usd}]
    deadline: Optional[str] = None          # ISO 8601, timezone-aware
    howto: Optional[str] = None
    # The only ranking signal in the system. The prototype computed it and threw it away.
    confidence: Optional[float] = Field(default=None, index=True)
    extractor_version: Optional[str] = None
    updated_at: datetime = Field(default_factory=_utcnow)


def create_sync_engine(url: str):
    return create_engine(url, echo=False)


def create_db_and_tables(sync_url: str):
    engine = create_sync_engine(sync_url)
    SQLModel.metadata.create_all(engine)
    _assert_schema_current(engine, sync_url)
    return engine


def _assert_schema_current(engine, sync_url: str) -> None:
    """Fail loudly on a pre-Phase-0 database rather than writing into a stale schema.

    `create_all` only creates missing tables; it will not add the unique constraint or the
    new columns to a database written by an older version. Silently continuing would let
    duplicate rows keep accumulating, which is the bug this schema exists to fix.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    for model in (Video, Giveaway):
        table = model.__tablename__
        if table not in inspector.get_table_names():
            continue
        present = {c["name"] for c in inspector.get_columns(table)}
        expected = set(model.model_fields)
        missing = expected - present
        unique_cols = {
            col
            for index in inspector.get_indexes(table)
            if index.get("unique")
            for col in index["column_names"]
        }
        if missing or "video_id" not in unique_cols:
            raise RuntimeError(
                f"Table '{table}' in {sync_url} uses an outdated schema "
                f"(missing columns: {sorted(missing) or 'none'}; "
                f"video_id unique index: {'video_id' in unique_cols}). "
                "The old database stored no raw descriptions and so has no value as a "
                "corpus. Delete or move it and let it be recreated:\n"
                "    mv prizes.db prizes.db.old"
            )


def get_sync_session(engine):
    if engine is None:
        raise RuntimeError("engine is None")
    return Session(engine)


def save_giveaway_sync(engine, video: dict, extracted: dict):
    """Upsert one video and its extracted giveaway, keyed on video_id.

    Previously this inserted unconditionally on every hit, which is why the prototype
    database held 213 rows for 93 distinct videos.
    """
    if engine is None:
        return
    video_id = video.get("id")
    if not video_id:
        return

    with Session(engine) as session:
        v = session.exec(select(Video).where(Video.video_id == video_id)).first()
        if v is None:
            v = Video(video_id=video_id)
            session.add(v)
        v.channel_id = video.get("channel_id")
        v.title = video.get("title")
        v.url = video.get("url")
        v.published_at = video.get("published_at")
        v.raw_description = video.get("description")
        v.fetched_at = _utcnow()

        g = session.exec(select(Giveaway).where(Giveaway.video_id == video_id)).first()
        if g is None:
            g = Giveaway(video_id=video_id)
            session.add(g)
        g.prizes = extracted.get("prizes")
        structured = extracted.get("prizes_structured")
        g.prizes_json = json.dumps(structured) if structured is not None else None
        g.deadline = extracted.get("deadline")
        g.howto = extracted.get("howto")
        g.confidence = extracted.get("confidence")
        g.extractor_version = extracted.get("extractor_version")
        g.updated_at = _utcnow()

        session.commit()
