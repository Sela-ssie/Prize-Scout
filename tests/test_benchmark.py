"""Measurement must never mutate, create, or disclose the indexed source store."""
import json

import pytest
from sqlalchemy import text

from scripts.benchmark import benchmark, main, readonly_engine
from src import db


def test_benchmark_runs_readonly_and_omits_source_data(tmp_path):
    path = tmp_path / 'index.db'
    engine = db.create_db_and_tables(f'sqlite:///{path}')
    db.save_giveaway_sync(engine, dict(id='v', title='PRIVATE SOURCE TITLE',
                          description='PRIVATE RAW TEXT'), dict(prizes='Camera'))
    engine.dispose()
    before = path.read_bytes()
    readonly = readonly_engine(f'sqlite:///{path}')
    try:
        with pytest.raises(Exception):
            with readonly.begin() as connection:
                connection.execute(text('DELETE FROM video'))
        report = benchmark(readonly, iterations=2, warmup=0)
        assert report['counts_stable']
        assert report['index']['indexed_videos'] == 1
        assert len(report['cases']) == 6
        assert next(case for case in report['cases'] if case['name'] == 'camera_search')['result_totals'] == [1]
        assert next(case for case in report['cases'] if case['name'] == 'empty_search')['result_totals'] == [0]
        assert 'PRIVATE' not in json.dumps(report)
        assert str(path) not in json.dumps(report)
    finally:
        readonly.dispose()
    assert path.read_bytes() == before


def test_missing_index_and_existing_reports_are_never_overwritten(tmp_path, monkeypatch):
    path = tmp_path / 'missing.db'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{path}')
    with pytest.raises(SystemExit):
        main([])
    assert not path.exists()
    engine = db.create_db_and_tables(f'sqlite:///{path}')
    engine.dispose()
    report = tmp_path / 'report.json'
    report.write_text('KEEP')
    with pytest.raises(SystemExit):
        main(['--iterations', '1', '--warmup', '0', '--out', str(report)])
    assert report.read_text() == 'KEEP'
