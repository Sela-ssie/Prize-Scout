#!/usr/bin/env python3
"""Create a consistent SQLite backup without copying a live WAL file."""
import argparse
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import src  # noqa: E402,F401 - loads local environment
from sqlalchemy.engine import make_url  # noqa: E402


def backup(source: Path, destination: Path):
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise ValueError('Backup must not overwrite the source database.')
    if not source.is_file():
        raise ValueError('Source database does not exist.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Refuse overwrites, including symlinks; a typo must not destroy a prior backup.
    with destination.open('xb'):
        pass
    try:
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as origin:
            with sqlite3.connect(destination) as target:
                origin.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed.')
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    url = make_url(os.environ.get('DATABASE_URL', 'sqlite:///./prizes.db'))
    if url.get_backend_name() != 'sqlite' or not url.database or url.database == ':memory:':
        parser.error('This backup command requires a file-backed SQLite DATABASE_URL.')
    try:
        backup(Path(url.database), args.out)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'Backup failed: {exc}\n')
    print(f'Backup verified: {args.out}')


if __name__ == '__main__':
    main()
