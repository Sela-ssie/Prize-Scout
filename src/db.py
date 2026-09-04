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
    title: Optional[str] = None
    url: Optional[str] = None
    fetched_at: datetime = Field(default_factory=_utcnow)


class Giveaway(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: str = Field(unique=True, index=True)
    prizes: Optional[str] = None
    deadline: Optional[str] = None
    howto: Optional[str] = None
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
        v.title = video.get("title")
        v.url = video.get("url")
        v.fetched_at = _utcnow()

        g = session.exec(select(Giveaway).where(Giveaway.video_id == video_id)).first()
        if g is None:
            g = Giveaway(video_id=video_id)
            session.add(g)
        g.prizes = extracted.get("prizes")
        g.deadline = extracted.get("deadline")
        g.howto = extracted.get("howto")
        g.updated_at = _utcnow()

        session.commit()
