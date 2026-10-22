import sqlite3

import pytest

from scripts.backup_db import backup


def test_backup_includes_committed_wal_and_refuses_overwrite(tmp_path):
    source, target = tmp_path / "live.db", tmp_path / "backup.db"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE example (value TEXT)")
        connection.execute("INSERT INTO example VALUES ('kept')")
        connection.commit()
        backup(source, target)
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT value FROM example").fetchone() == ("kept",)
    with pytest.raises(FileExistsError):
        backup(source, target)
    with pytest.raises(ValueError, match="source"):
        backup(source, source)


def test_missing_source_does_not_create_empty_database(tmp_path):
    source, target = tmp_path / "missing.db", tmp_path / "backup.db"
    with pytest.raises(ValueError, match="does not exist"):
        backup(source, target)
    assert not source.exists()
    assert not target.exists()
