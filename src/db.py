import os
from typing import Optional

try:
    from sqlmodel import SQLModel, Field, create_engine, Session
    SQLMODEL_AVAILABLE = True
except Exception:
    # sqlmodel (and its deps) not installed in this environment
    SQLMODEL_AVAILABLE = False

DATABASE_URL = os.environ.get("DATABASE_URL")


if SQLMODEL_AVAILABLE:
    class Video(SQLModel, table=True):
        id: Optional[int] = Field(default=None, primary_key=True)
        video_id: str
        title: Optional[str]
        url: Optional[str]


    class Giveaway(SQLModel, table=True):
        id: Optional[int] = Field(default=None, primary_key=True)
        video_id: str
        prizes: Optional[str]
        deadline: Optional[str]
        howto: Optional[str]


def create_sync_engine(url: str):
    if not SQLMODEL_AVAILABLE:
        return None
    return create_engine(url, echo=False)


def create_db_and_tables(sync_url: str):
    if not SQLMODEL_AVAILABLE:
        # nothing to do if sqlmodel is not installed
        return None
    engine = create_sync_engine(sync_url)
    SQLModel.metadata.create_all(engine)
    return engine


def get_sync_session(engine):
    if not SQLMODEL_AVAILABLE or engine is None:
        raise RuntimeError("SQLModel not available or engine is None")
    return Session(engine)


def save_giveaway_sync(engine, video: dict, extracted: dict):
    """Synchronous persistence helper. No-op if sqlmodel not available."""
    if not SQLMODEL_AVAILABLE or engine is None:
        return
    with Session(engine) as session:
        v = Video(video_id=video.get('id'), title=video.get('title'), url=video.get('url'))
        session.add(v)
        g = Giveaway(video_id=video.get('id'), prizes=extracted.get('prizes'), deadline=extracted.get('deadline'), howto=extracted.get('howto'))
        session.add(g)
        session.commit()


