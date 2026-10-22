"""Promotions remain unverified, source-bound and retention-limited in the index."""
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session, select

from src import api, db

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(db, '_utcnow', lambda: NOW)
    return db.create_db_and_tables('sqlite:///' + str(tmp_path / 'promotions.db'))


def save(engine, video_id='video', description='Use code MixedCase for 20% off.'):
    db.save_giveaway_sync(engine, dict(id=video_id, title='A product review',
                                     url='https://www.youtube.com/watch?v=' + video_id,
                                     description=description), None)


def test_negative_video_still_persists_code_candidate(engine):
    save(engine)
    result = db.list_promotions(engine)
    assert result['total'] == 1
    candidate = result['results'][0]
    assert candidate['kind'] == 'promo_code' and candidate['code'] == 'MixedCase'
    assert candidate['video_id'] == 'video' and candidate['video_title'] == 'A product review'
    assert candidate['source_checked_at']
    assert candidate['last_checked_at'] is None
    assert candidate['redemption_verified'] is False
    assert candidate['availability'] == 'unverified'
    with Session(engine) as session:
        assert session.exec(select(db.Giveaway)).all() == []


def test_source_refresh_transactionally_replaces_or_removes_old_codes(engine):
    save(engine)
    save(engine, description='Coupon code NewCode gives 15% off.')
    assert [row['code'] for row in db.list_promotions(engine)['results']] == ['NewCode']
    save(engine, description='The description no longer contains a promotion.')
    assert db.list_promotions(engine)['total'] == 0
    with Session(engine) as session:
        assert session.exec(select(db.PromotionMention)).all() == []


def test_offline_replay_preserves_fetch_clock_and_does_not_claim_check(engine):
    save(engine)
    original = NOW - timedelta(days=5)
    with Session(engine) as session:
        video = session.exec(select(db.Video).where(db.Video.video_id == 'video')).one()
        video.fetched_at = original
        session.add(video)
        session.commit()
    assert db.update_extraction_sync(engine, 'video', None)
    candidate = db.list_promotions(engine)['results'][0]
    assert candidate['code'] == 'MixedCase'
    assert candidate['last_checked_at'] is None and candidate['redemption_verified'] is False
    with Session(engine) as session:
        fetched = session.exec(select(db.Video.fetched_at).where(db.Video.video_id == 'video')).one()
        assert fetched.replace(tzinfo=timezone.utc) == original


def test_remove_videos_removes_promotion_payloads(engine):
    save(engine)
    db.remove_videos(engine, ['video'])
    assert db.list_promotions(engine)['total'] == 0
    with Session(engine) as session:
        assert session.exec(select(db.PromotionMention)).all() == []


def test_stale_source_hidden_from_promotions_inbox(engine):
    save(engine)
    with Session(engine) as session:
        video = session.exec(select(db.Video).where(db.Video.video_id == 'video')).one()
        video.fetched_at = NOW - timedelta(days=30, seconds=1)
        session.add(video)
        session.commit()
    assert db.list_promotions(engine)['total'] == 0


def test_source_expiry_recomputed_without_redemption_check(engine, monkeypatch):
    save(engine, description='Promo code Soon gives 20% off, expires 2026-10-17.')
    before = db.list_promotions(engine)['results'][0]
    assert before['availability'] == 'unverified'
    monkeypatch.setattr(db, '_utcnow', lambda: NOW + timedelta(days=3))
    after = db.list_promotions(engine)['results'][0]
    assert after['availability'] == 'expired'
    assert after['last_checked_at'] is None and after['redemption_verified'] is False


def test_affiliate_only_mention_remains_distinct_from_discount(engine):
    save(engine, description='Affiliate link: https://merchant.example.com/products')
    candidate = db.list_promotions(engine)['results'][0]
    assert candidate['kind'] == 'affiliate_link'
    assert candidate['code'] is None and candidate['benefit_claim'] is None
    assert candidate['redemption_verified'] is False


def test_promotions_api_paginates_and_never_claims_verified_redemption(engine, monkeypatch):
    save(engine, 'one')
    save(engine, 'two', 'Promo code Other gives 10% off.')
    monkeypatch.setattr(api, 'get_engine', lambda: engine)
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    with TestClient(app) as client:
        response = client.get('/api/promotions', params=dict(page=1, page_size=1))
        assert response.status_code == 200
        first = response.json()
        assert first['total'] == 2 and first['has_more'] is True
        assert first['page'] == first['page_size'] == 1
        second = client.get('/api/promotions', params=dict(page=2, page_size=1)).json()
        assert second['has_more'] is False
        assert first['results'][0]['video_id'] != second['results'][0]['video_id']
        assert all(row['redemption_verified'] is False and row['last_checked_at'] is None
                   and row['availability'] == 'unverified' for row in first['results'] + second['results'])
        assert client.get('/api/promotions?page=0').status_code == 422
        assert client.get('/api/promotions?page_size=101').status_code == 422
