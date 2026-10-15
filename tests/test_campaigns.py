from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from src import api, db
from src.campaigns import campaign_identity, normalize_entry_url


@pytest.mark.parametrize('url', [
    'https://gleam.io/Ab123/book-draw?utm_source=creator',
    'http://www.gleam.io/Ab123/another-title#entry',
])
def test_campaign_paths_ignore_referral_and_slug(url):
    assert normalize_entry_url(url) == 'https://gleam.io/Ab123'


@pytest.mark.parametrize('url', [
    'https://gleam.io', 'https://gleam.io/competitions',
    'https://gleam.io.evil.example/Ab123', 'https://evil.example/gleam.io/Ab123',
    'https://user:password@gleam.io/Ab123', 'https://gleam.io:443/Ab123',
    'https://example.com/affiliate', 'https://bit.ly/same-link',
])
def test_generic_affiliate_shortened_and_lookalike_urls_do_not_group(url):
    assert normalize_entry_url(url) is None


def test_ambiguous_links_remain_separate_and_ids_are_case_sensitive():
    assert campaign_identity('https://gleam.io/Ab123 and https://gleam.io/Zx987') == (None, None)
    assert campaign_identity('https://gleam.io/Ab123') != campaign_identity('https://gleam.io/ab123')
    assert campaign_identity('https://gleam.io/Ab123/a https://gleam.io/Ab123/b')[0]


@pytest.fixture
def index(tmp_path, monkeypatch):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path}/campaigns.db')
    monkeypatch.setattr(api, 'get_engine', lambda: engine)
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    return engine, TestClient(app)


def save(engine, video_id, link='https://gleam.io/Ab123/books', *, confidence=.9, prize='book', title=None, deadline=None):
    description = f'Giveaway! Win a {prize}. To enter, comment below. Enter here: {link}'
    db.save_giveaway_sync(engine, {'id': video_id, 'title': title or f'{prize} giveaway',
        'description': description, 'published_at': '2026-10-01T12:00:00Z'},
        {'prizes': prize, 'prizes_structured': [{'item': prize, 'value_usd': None}],
         'confidence': confidence, 'howto': 'To enter, comment below.', 'deadline': deadline,
         'availability': 'active' if deadline else 'unknown'})


def test_grouped_pagination_counts_campaigns_and_filter_finds_other_members(index):
    engine, browser = index
    save(engine, 'a', confidence=.95, title='Exclusive signed bundle')
    save(engine, 'b', confidence=.85)
    save(engine, 'c', link='https://gleam.io/Zx987/another', confidence=.8)
    save(engine, 'd', link='https://example.com/shop', confidence=.7)
    assert browser.get('/api/giveaways').json()['total'] == 4
    first = browser.get('/api/giveaways?group_campaigns=true&page_size=1').json()
    assert first['total'] == 3 and first['has_more']
    campaign = first['results'][0]['campaign']
    assert first['results'][0]['video_id'] == 'a'
    assert campaign['source_count'] == 2
    assert {s['video_id'] for s in campaign['sources']} == {'a', 'b'}
    second = browser.get('/api/giveaways?group_campaigns=true&page_size=1&page=2').json()
    assert second['results'][0]['video_id'] == 'c'
    third = browser.get('/api/giveaways?group_campaigns=true&page_size=1&page=3').json()
    assert third['results'][0]['video_id'] == 'd' and not third['has_more']
    matching = browser.get('/api/search?q=exclusive&group_campaigns=true').json()
    assert matching['total'] == 1 and matching['results'][0]['campaign']['source_count'] == 2


def test_differences_are_disclosed_and_prize_evidence_roundtrips(index):
    engine, browser = index
    save(engine, 'a', prize='book', deadline='2030-01-01T23:59:59+00:00')
    save(engine, 'b', prize='novel', deadline='2030-02-01T23:59:59+00:00')
    result = browser.get('/api/giveaways?group_campaigns=true').json()['results'][0]
    assert set(result['campaign']['conflicting_fields']) == {'prizes', 'deadline'}
    assert result['evidence']['prizes'][0]['quote'] == 'book'
    assert result['campaign']['sources'][1]['evidence']['prizes'][0]['quote'] == 'novel'


def test_replay_updates_membership_without_renewing_source_and_removal_recounts(index):
    engine, browser = index
    save(engine, 'a')
    save(engine, 'b')
    with Session(engine) as session:
        video = session.exec(select(db.Video).where(db.Video.video_id == 'b')).one()
        fetched = video.fetched_at
        video.raw_description = 'Giveaway! Win a book. Subscribe to enter: https://gleam.io/Zx987/new'
        session.add(video)
        session.commit()
    db.update_extraction_sync(engine, 'b', {'prizes': 'book', 'confidence': .9})
    assert browser.get('/api/giveaways?group_campaigns=true').json()['total'] == 2
    with Session(engine) as session:
        assert session.exec(select(db.Video).where(db.Video.video_id == 'b')).one().fetched_at == fetched
    db.remove_videos(engine, ['b'])
    result = browser.get('/api/giveaways?group_campaigns=true').json()['results'][0]
    assert result['campaign']['source_count'] == 1


def test_stale_related_sources_are_never_exposed(index):
    engine, browser = index
    save(engine, 'a')
    save(engine, 'b')
    with Session(engine) as session:
        video = session.exec(select(db.Video).where(db.Video.video_id == 'b')).one()
        video.fetched_at = datetime.now(timezone.utc) - timedelta(days=31)
        session.add(video)
        session.commit()
    result = browser.get('/api/giveaways?group_campaigns=true').json()
    assert result['total'] == 1
    assert result['results'][0]['campaign']['source_count'] == 1
    assert all(s['video_id'] != 'b' for s in result['results'][0]['campaign']['sources'])
