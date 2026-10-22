"""Discovery requires supported recent availability, including grouped results."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from src import api, db

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
DEADLINE = '2026-10-31T23:59:59+00:00'


@pytest.fixture
def index(tmp_path, monkeypatch):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path / "discovery.db"}')
    clock = [NOW]
    monkeypatch.setattr(db, '_utcnow', lambda: clock[0])
    monkeypatch.setattr(api, 'get_engine', lambda: engine)
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    with TestClient(app) as browser:
        yield engine, browser, clock
    engine.dispose()


def extraction(confidence=.9):
    return dict(prizes='Book', prizes_structured=[dict(item='Book', value_usd=None)],
        confidence=confidence, deadline=DEADLINE, availability='active', howto='Comment to enter.')


def save(engine, video_id, *, supported=True, confidence=.9, campaign='Ab123', title='Book giveaway'):
    description = ('Giveaway ends October 31, 2026.' if supported else 'Sale ends October 31, 2026.')
    description += f'\nComment to enter.\nhttps://gleam.io/{campaign}/book'
    db.save_giveaway_sync(engine, dict(id=video_id, title=title, description=description,
        channel_id='source', channel_title='Source', published_at='2026-10-16T00:00:00Z'), extraction(confidence))


def test_current_filter_requires_evidence_and_returns_assessment(index):
    engine, browser, _ = index
    save(engine, 'supported')
    save(engine, 'unsupported', supported=False)
    result = browser.get('/api/giveaways', params={'current_only': 'true'}).json()
    assert result['total'] == 1
    assert result['results'][0]['video_id'] == 'supported'
    assessment = result['results'][0]['availability_assessment']
    assert assessment['state'] == 'source_reported_open'
    assert assessment['discoverable'] is True
    assert result['index_stats']['current_evidence_mentions'] == 1
    assert result['index_stats']['unconfirmed_mentions'] == 1
    mentions = browser.get('/api/giveaways').json()
    assert mentions['total'] == 2
    unconfirmed = next(row for row in mentions['results'] if row['video_id'] == 'unsupported')
    assert unconfirmed['availability_assessment']['state'] == 'unconfirmed'


def test_offline_replay_does_not_renew_source_or_make_stale_check_current(index):
    engine, browser, clock = index
    clock[0] = NOW - timedelta(hours=49)
    save(engine, 'replayed')
    with Session(engine) as session:
        original = session.exec(select(db.Video).where(db.Video.video_id == 'replayed')).one().fetched_at
    clock[0] = NOW
    assert db.update_extraction_sync(engine, 'replayed', extraction()) is True
    with Session(engine) as session:
        video = session.exec(select(db.Video).where(db.Video.video_id == 'replayed')).one()
        giveaway = session.exec(select(db.Giveaway).where(db.Giveaway.video_id == 'replayed')).one()
        assert video.fetched_at == original
        assert giveaway.current_evidence_supported is True  # Support survives age; runtime freshness gates discovery.
    assert browser.get('/api/giveaways?current_only=true').json()['total'] == 0
    row = browser.get('/api/giveaways').json()['results'][0]
    assert row['availability_assessment']['state'] == 'needs_recheck'
    assert row['availability_assessment']['discoverable'] is False


def test_runtime_clock_enforces_ttl_boundary_and_future_fetch(index):
    engine, browser, clock = index
    save(engine, 'timed')
    clock[0] = NOW + timedelta(hours=48)
    assert browser.get('/api/giveaways?current_only=true').json()['total'] == 1
    clock[0] += timedelta(seconds=1)
    assert browser.get('/api/giveaways?current_only=true').json()['total'] == 0
    row = browser.get('/api/giveaways').json()['results'][0]
    assert row['availability_assessment']['state'] == 'needs_recheck'
    clock[0] = NOW - timedelta(seconds=1)
    assert browser.get('/api/giveaways?current_only=true').json()['total'] == 0
    row = browser.get('/api/giveaways').json()['results'][0]
    assert row['availability_assessment']['state'] == 'unconfirmed'


def test_current_filter_precedes_campaign_ranking_and_pagination(index):
    engine, browser, _ = index
    save(engine, 'unsupported-high', supported=False, confidence=.99)
    save(engine, 'supported-low', confidence=.8)
    save(engine, 'other', confidence=.7, campaign='Zx987')
    params = dict(current_only='true', group_campaigns='true', page_size=1)
    first = browser.get('/api/giveaways', params=params).json()
    assert first['total'] == 2 and first['has_more'] is True
    assert first['results'][0]['video_id'] == 'supported-low'
    if 'campaign' in first['results'][0]:
        members = first['results'][0]['campaign']['sources']
        rejected = next(row for row in members if row['video_id'] == 'unsupported-high')
        assert rejected['availability_assessment']['discoverable'] is False
    second = browser.get('/api/giveaways', params=params | {'page': 2}).json()
    assert second['results'][0]['video_id'] == 'other'
    assert second['has_more'] is False


def test_closed_related_source_withholds_campaign_and_discloses_conflict(index):
    engine, browser, _ = index
    save(engine, 'open', confidence=.8)
    save(engine, 'closed', confidence=.99, title='Book giveaway is now closed')
    assert browser.get('/api/giveaways?current_only=true&group_campaigns=true').json()['total'] == 0
    assert browser.get('/api/giveaways?current_only=true').json()['total'] == 0
    all_mentions = browser.get('/api/giveaways').json()
    rows = {row['video_id']: row for row in all_mentions['results']}
    assert rows['closed']['availability_assessment']['state'] == 'closed'
    assert rows['closed']['availability_assessment']['discoverable'] is False
    assessment = rows['open']['availability_assessment']
    assert assessment['state'] == 'unconfirmed'
    assert assessment['discoverable'] is False
    assert 'campaign_reports_closed' in assessment['reasons']
    assert all_mentions['index_stats']['current_evidence_mentions'] == 0


def save_negative_closure(engine, video_id='negative-closure'):
    db.save_giveaway_sync(engine, dict(id=video_id, title='Book giveaway is now closed',
        description='The giveaway has ended.\nhttps://gleam.io/Ab123/book',
        channel_id='source', channel_title='Source'), None)


@pytest.mark.parametrize('grouped', [False, True])
def test_negative_closure_filters_before_pagination_and_stats(index, grouped):
    engine, browser, _ = index
    save(engine, 'blocked-high', confidence=.99)
    save(engine, 'other', confidence=.7, campaign='Zx987')
    save_negative_closure(engine)
    with Session(engine) as session:
        assert session.exec(select(db.Giveaway).where(db.Giveaway.video_id == 'negative-closure')).first() is None
        source = session.exec(select(db.Video).where(db.Video.video_id == 'negative-closure')).one()
        assert source.source_reports_closed is True
        assert source.campaign_key is not None
    result = browser.get('/api/giveaways', params=dict(current_only='true',
        group_campaigns=str(grouped).lower(), page_size=1)).json()
    assert result['total'] == 1
    assert result['has_more'] is False
    assert result['results'][0]['video_id'] == 'other'
    assert result['index_stats']['current_evidence_mentions'] == 1
    assert result['index_stats']['unconfirmed_mentions'] == 1
    all_mentions = browser.get('/api/giveaways').json()
    blocked = next(row for row in all_mentions['results'] if row['video_id'] == 'blocked-high')
    assert blocked['availability_assessment']['discoverable'] is False
    assert 'campaign_reports_closed' in blocked['availability_assessment']['reasons']


def test_removing_negative_closure_clears_campaign_gate(index):
    engine, browser, _ = index
    save(engine, 'open')
    save_negative_closure(engine)
    assert browser.get('/api/giveaways?current_only=true').json()['total'] == 0
    db.remove_videos(engine, ['negative-closure'])
    result = browser.get('/api/giveaways?current_only=true').json()
    assert result['total'] == 1
    assert result['results'][0]['availability_assessment']['discoverable'] is True


def test_stale_or_future_closure_does_not_block_current_source(index):
    engine, browser, clock = index
    clock[0] = NOW - timedelta(days=31)
    save_negative_closure(engine, 'stale-closure')
    clock[0] = NOW + timedelta(seconds=1)
    save_negative_closure(engine, 'future-closure')
    clock[0] = NOW
    save(engine, 'open')
    result = browser.get('/api/giveaways?current_only=true').json()
    assert result['total'] == 1
    assert result['results'][0]['availability_assessment']['discoverable'] is True


def test_supported_passed_deadline_negative_source_suppresses_campaign(index):
    engine, browser, _ = index
    save(engine, 'open')
    db.save_giveaway_sync(engine, dict(id='past-negative', title='Book giveaway',
        description='Giveaway ends October 15, 2026.\nhttps://gleam.io/Ab123/book',
        published_at='2026-10-14T00:00:00Z'), None,
        analysis=dict(is_giveaway=False, confidence=0, extractor_version='test',
            rejected_because='expired', deadline='2026-10-15T23:59:59+00:00', availability='expired'))
    with Session(engine) as session:
        assert session.exec(select(db.Giveaway).where(db.Giveaway.video_id == 'past-negative')).first() is None
        source = session.exec(select(db.Video).where(db.Video.video_id == 'past-negative')).one()
        assert source.source_reports_closed is True
    assert browser.get('/api/giveaways?current_only=true').json()['total'] == 0
    assert browser.get('/api/giveaways?current_only=true&group_campaigns=true').json()['total'] == 0
    result = browser.get('/api/giveaways').json()['results'][0]
    assert result['availability_assessment']['state'] == 'unconfirmed'
    assert 'campaign_reports_closed' in result['availability_assessment']['reasons']


def test_winner_announcement_negative_source_overrides_old_future_claim(index):
    engine, browser, _ = index
    save(engine, 'open')
    db.save_giveaway_sync(engine, dict(id='winner-announcement', title='Giveaway winner announced',
        description='Entries close October 31, 2026.\nhttps://gleam.io/Ab123/book',
        published_at='2026-10-16T00:00:00Z'), None)
    assert browser.get('/api/giveaways?current_only=true').json()['total'] == 0
    result = browser.get('/api/giveaways').json()['results'][0]
    assert result['availability_assessment']['reasons'] == ['campaign_reports_closed']


def test_future_opening_period_stays_out_of_current_discovery(index):
    engine, browser, _ = index
    db.save_giveaway_sync(engine, dict(id='not-started', title='Book giveaway',
        description='Entries open October 25, 2026.\nGiveaway ends October 31, 2026.',
        published_at='2026-10-16T00:00:00Z'), extraction())
    assert browser.get('/api/giveaways?current_only=true').json()['total'] == 0
    row = browser.get('/api/giveaways').json()['results'][0]
    assert row['availability_assessment']['reasons'] == ['entry_opening_requires_review']
