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


def test_outdated_schema_is_upgraded_without_losing_data(tmp_path):
    """Legacy rows survive additive upgrades and become refresh candidates."""
    import sqlite3

    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE video (id INTEGER NOT NULL PRIMARY KEY, video_id VARCHAR NOT NULL, title VARCHAR, url VARCHAR)")
    con.execute("CREATE TABLE giveaway (id INTEGER NOT NULL PRIMARY KEY, video_id VARCHAR NOT NULL, prizes VARCHAR, deadline VARCHAR, howto VARCHAR)")
    con.execute("INSERT INTO video(video_id,title) VALUES ('legacy','Original')")
    con.execute("INSERT INTO giveaway(video_id,prizes) VALUES ('legacy','Camera')")
    con.commit()
    con.close()

    engine = db.create_db_and_tables(f"sqlite:///{path}")
    with db.get_sync_session(engine) as session:
        assert session.exec(select(db.Video)).one().title == "Original"
        assert session.exec(select(db.Giveaway)).one().prizes == "Camera"
        assert session.exec(select(db.Giveaway)).one().availability == "unknown"
    db.create_db_and_tables(f"sqlite:///{path}")


def test_raw_description_and_confidence_are_persisted(tmp_path):
    """The prototype stored neither, so the corpus was unusable and ranking impossible."""
    engine = _engine(tmp_path)
    video = {
        "id": "vid1",
        "channel_id": "UC123",
        "title": "Giveaway",
        "url": "https://y/vid1",
        "published_at": "2026-01-01T00:00:00Z",
        "description": "Full raw description text that must survive the round trip.",
    }
    extracted = {
        "prizes": "PlayStation 5 ($499)",
        "prizes_structured": [{"item": "PlayStation 5", "value_usd": 499.0}],
        "deadline": "2026-12-01T00:00:00+00:00",
        "howto": "Subscribe and comment.",
        "confidence": 0.87,
        "extractor_version": "2.0.0",
    }
    db.save_giveaway_sync(engine, video, extracted)

    with db.get_sync_session(engine) as session:
        v = session.exec(select(db.Video)).one()
        g = session.exec(select(db.Giveaway)).one()

    assert v.raw_description == video["description"]
    assert v.channel_id == "UC123"
    assert v.published_at == "2026-01-01T00:00:00Z"
    assert g.confidence == 0.87
    assert g.extractor_version == "2.0.0"
    import json as _json
    assert _json.loads(g.prizes_json) == extracted["prizes_structured"]


def test_duplicate_legacy_rows_are_archived_before_consolidation(tmp_path):
    import sqlite3
    path = tmp_path / 'duplicates.db'
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE video (id INTEGER PRIMARY KEY, video_id VARCHAR, title VARCHAR)')
    con.execute("INSERT INTO video VALUES (1, 'v', 'First')")
    con.execute("INSERT INTO video VALUES (2, 'v', 'Latest')")
    con.commit()
    con.close()
    engine = db.create_db_and_tables(f'sqlite:///{path}')
    with db.get_sync_session(engine) as session:
        assert session.exec(select(db.Video)).one().title == 'Latest'
    con = sqlite3.connect(path)
    assert con.execute('SELECT title FROM video_legacy_archive').fetchone()[0] == 'First'
    con.close()
