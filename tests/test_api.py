from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from src import api, db


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path}/index.db')
    monkeypatch.setattr(api, 'get_engine', lambda: engine)
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    return TestClient(app), engine


def test_local_search_paginates_and_joins_metadata(client):
    browser, engine = client
    for i in range(3):
        db.save_giveaway_sync(engine, {'id': str(i), 'title': f'Camera giveaway {i}', 'description': 'A camera for viewers'},
                              {'prizes': 'Camera', 'confidence': .9-i/10, 'availability': 'unknown'})
    first = browser.get('/api/search', params={'q': 'camera', 'page_size': 1}).json()
    assert first['total'] == 3 and first['has_more']
    assert first['results'][0]['video_title'] == 'Camera giveaway 0'
    fetched_at = datetime.fromisoformat(first['results'][0]['fetched_at'])
    assert fetched_at.utcoffset() == timedelta(0)
    second = browser.get('/api/search', params={'q': 'camera', 'page_size': 1, 'page': 2}).json()
    assert second['results'][0]['video_id'] == '1'
    assert browser.get('/api/search?q=absent').json()['total'] == 0
    assert browser.get('/api/giveaways?min_confidence=0.85').json()['total'] == 1
    assert browser.get('/api/health').json()['indexed_videos'] == 3


def test_expired_rows_excluded_and_filters_validated(client):
    browser, engine = client
    db.save_giveaway_sync(engine, {'id': 'old', 'title': 'Camera'},
                          {'confidence': .9, 'deadline': '2020-01-01T00:00:00+00:00', 'availability': 'active'})
    assert browser.get('/api/search').json()['total'] == 0
    expired = browser.get('/api/giveaways?availability=expired').json()
    assert expired['results'][0]['availability'] == 'expired'
    for query in ['page=0', 'page_size=101', 'min_confidence=2', 'availability=open', 'category=invalid', 'sort=invalid']:
        assert browser.get('/api/search?' + query).status_code == 422
    assert browser.get('/api/search?q=%22%20OR%20%2A').status_code == 200


def test_category_api_and_filters(client):
    browser, engine = client
    db.save_giveaway_sync(engine, {'id': 'book', 'title': 'Signed novel giveaway'}, {'prizes': 'Signed novel'})
    assert browser.get('/api/search?category=books&sort=newest').json()['total'] == 1
    assert browser.get('/api/search?category=tech').json()['total'] == 0
    categories = browser.get('/api/categories').json()['categories']
    assert next(c for c in categories if c['id'] == 'books')['count'] == 1
    assert browser.get('/api/giveaways').json()['index_stats']['giveaways'] == 1


def test_coverage_exposes_counts_without_source_text_or_guesses(client):
    browser, engine = client
    db.save_giveaway_sync(engine, {'id': 'legacy', 'title': 'PRIVATE TITLE',
                                  'description': 'PRIVATE DESCRIPTION'}, {'prizes': 'Camera'})
    response = browser.get('/api/coverage')
    assert response.status_code == 200
    assert response.json()['totals']['indexed_videos'] == 1
    assert response.json()['totals']['unanalyzed_videos'] == 1
    assert response.json()['sources'][0]['channel_id'] == ''
    assert 'PRIVATE' not in response.text
    assert 'confidence' not in response.text


def test_availability_requires_parseable_future_deadline(client):
    browser, engine = client
    for video_id, deadline, availability in [('legacy', None, 'active'),
            ('invalid', 'bad-date', 'active'), ('future', '2099-01-01', 'unknown'),
            ('closed', '2099-01-01', 'expired')]:
        db.save_giveaway_sync(engine, {'id': video_id},
                              {'prizes': 'Camera', 'deadline': deadline, 'availability': availability})
    assert {row['video_id'] for row in browser.get('/api/giveaways?availability=unknown').json()['results']} == {'legacy', 'invalid'}
    assert [row['video_id'] for row in browser.get('/api/giveaways?availability=active').json()['results']] == ['future']
    assert [row['video_id'] for row in browser.get('/api/giveaways?availability=expired').json()['results']] == ['closed']


def test_search_includes_normalized_prize_names(client):
    browser, engine = client
    db.save_giveaway_sync(engine, {'id': 'cash', 'title': '$100 giveaway', 'description': 'Enter below'}, {'prizes': 'cash'})
    assert browser.get('/api/search?q=cash').json()['total'] == 1


def test_database_errors_are_sanitized_and_retryable(monkeypatch):
    from sqlalchemy.exc import OperationalError
    from src.main import app
    def unavailable():
        raise OperationalError('private SQL', {'secret': 'private input'}, Exception('database locked'))
    monkeypatch.setattr(api, 'get_engine', unavailable)
    browser = TestClient(app, raise_server_exceptions=False)
    response = browser.get('/api/search')
    assert response.status_code == 503
    assert response.headers['retry-after'] == '30'
    assert 'private' not in response.text


def test_negative_updates_remove_match_and_fts_updates(client):
    browser, engine = client
    db.save_giveaway_sync(engine, {'id': 'v', 'title': 'Camera'}, {'prizes': 'Camera'})
    db.save_giveaway_sync(engine, {'id': 'v', 'title': 'Tablet', 'description': 'Changed'}, {'prizes': 'Tablet'})
    assert browser.get('/api/search?q=camera').json()['total'] == 0
    assert browser.get('/api/search?q=tablet').json()['total'] == 1
    db.save_giveaway_sync(engine, {'id': 'v', 'title': 'Tablet', 'description': 'No giveaway'})
    assert browser.get('/api/giveaways').json()['total'] == 0
    assert browser.get('/api/health').json()['indexed_videos'] == 1


def test_api_hides_stale_data_without_network_refresh(client):
    from datetime import datetime, timedelta, timezone
    from sqlmodel import Session, select
    browser, engine = client
    db.save_giveaway_sync(engine, {'id': 'v', 'title': 'Camera'}, {'prizes': 'Camera'})
    with Session(engine) as session:
        v = session.exec(select(db.Video)).one()
        v.fetched_at = datetime.now(timezone.utc)-timedelta(days=31)
        session.add(v)
        session.commit()
    assert browser.get('/api/search?q=camera').json()['total'] == 0
