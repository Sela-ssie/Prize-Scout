#!/usr/bin/env python3
"""Preview PrizeScout with fictional examples in a disposable database, without API calls."""
import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--check', action='store_true', help='validate demo data without starting a server')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if not args.check and not (ROOT / 'frontend/dist/index.html').is_file():
        parser.error('Build the UI first: cd frontend && npm ci && npm run build')
    with tempfile.TemporaryDirectory(prefix='prizescout-demo-') as directory:
        # Override .env deliberately: a preview must never write to the user's index.
        os.environ['DATABASE_URL'] = f'sqlite:///{directory}/demo.db'
        os.environ['PRIZESCOUT_DEMO'] = '1'
        os.environ['INGESTION_ENABLED'] = '0'
        from src import db
        from src.demo import seed_demo
        engine = db.create_db_and_tables(os.environ['DATABASE_URL'])
        count = seed_demo(engine)
        engine.dispose()
        print(f'Fictional demo: {count} examples. No real giveaways or YouTube requests.', flush=True)
        if args.check:
            from fastapi.testclient import TestClient
            from src.main import app
            with TestClient(app) as client:
                response = client.get('/api/giveaways')
                assert response.status_code == 200
                data = response.json()
                assert data['total'] == count and data['index_stats']['demo']
                assert all(result['is_demo'] for result in data['results'])
                grouped = client.get('/api/giveaways?group_campaigns=true').json()
                assert grouped['total'] == count - 1
                assert any(result.get('campaign', {}).get('source_count') == 2 for result in grouped['results'])
                coverage = client.get('/api/coverage').json()
                assert coverage['totals']['indexed_videos'] == count
                assert coverage['totals']['analyzed_videos'] == count
                assert coverage['totals']['source_count'] == 6
            from src.api import get_engine
            get_engine().dispose()
        else:
            import uvicorn
            uvicorn.run('src.main:app', host='127.0.0.1', port=args.port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
