from datetime import timezone

from sqlmodel import select

from src import db


def _engine(tmp_path):
    return db.create_db_and_tables(f"sqlite:///{tmp_path}/test.db")


def test_repeated_saves_upsert_instead_of_duplicating(tmp_path):
    """The prototype inserted a new row per hit: 213 rows for 93 distinct videos."""
    engine = _engine(tmp_path)
    video = {"id": "abc123", "title": "Giveaway!", "url": "https://y/abc123"}

    for prizes in ("PS5", "PS5", "PS5 and $500"):
        db.save_giveaway_sync(engine, video, {"prizes": prizes, "deadline": None, "howto": None})

    with db.get_sync_session(engine) as session:
        videos = session.exec(select(db.Video)).all()
        giveaways = session.exec(select(db.Giveaway)).all()

    assert len(videos) == 1
    assert len(giveaways) == 1
    assert giveaways[0].prizes == "PS5 and $500"  # last write wins


def test_distinct_videos_get_distinct_rows(tmp_path):
    engine = _engine(tmp_path)
    for vid in ("a", "b", "c"):
        db.save_giveaway_sync(engine, {"id": vid, "title": vid, "url": vid}, {"prizes": vid})

    with db.get_sync_session(engine) as session:
        assert len(session.exec(select(db.Giveaway)).all()) == 3


def test_timestamps_are_timezone_aware(tmp_path):
    engine = _engine(tmp_path)
    db.save_giveaway_sync(engine, {"id": "z", "title": "t", "url": "u"}, {"prizes": "p"})
    row = db.Video(video_id="scratch")
    assert row.fetched_at.tzinfo is not None
    assert row.fetched_at.utcoffset() == timezone.utc.utcoffset(None)


def test_outdated_schema_is_rejected(tmp_path):
    """A pre-Phase-0 database must fail loudly, not silently accumulate duplicates."""
    import sqlite3

    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE video (id INTEGER NOT NULL PRIMARY KEY, video_id VARCHAR NOT NULL, title VARCHAR, url VARCHAR)")
    con.execute("CREATE TABLE giveaway (id INTEGER NOT NULL PRIMARY KEY, video_id VARCHAR NOT NULL, prizes VARCHAR, deadline VARCHAR, howto VARCHAR)")
    con.commit()
    con.close()

    import pytest

    with pytest.raises(RuntimeError, match="outdated schema"):
        db.create_db_and_tables(f"sqlite:///{path}")
