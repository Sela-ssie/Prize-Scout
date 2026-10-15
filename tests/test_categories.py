from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from src import db
from src.categories import classify_prizes


@pytest.mark.parametrize(('item', 'category'), [
    ('signed books', 'books'), ('skincare bundle', 'beauty'), ('hoodie', 'fashion'),
    ('air fryer', 'home'), ('chocolate hamper', 'food'), ('hotel getaway', 'travel'),
    ('camping tent', 'sports'), ('watercolor paint set', 'arts'), ('dog toys', 'pets'),
    ('Tesla', 'automotive'), ('cash', 'cash'), ('PlayStation 5', 'gaming'), ('iPhone', 'tech'),
    ('mystery surprise', 'other'),
])
def test_categories_follow_identified_prize(item, category):
    assert category in classify_prizes([{'item': item}])


def test_categories_can_overlap_and_do_not_match_substrings():
    assert classify_prizes([{'item': 'bookcase'}]) == ['other']
    assert set(classify_prizes([{'item': 'gaming PC'}, {'item': 'gift card'}])) == {'tech', 'gaming', 'cash'}
    assert classify_prizes([{'item': 'TV stand'}, {'item': 'coffee table'}]) == ['home']


def test_offline_categories_migrate_without_renewing_source(tmp_path):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path}/index.db')
    db.save_giveaway_sync(engine, {'id': 'book', 'description': 'Saved source'}, {'prizes': 'Signed books'})
    with Session(engine) as session:
        giveaway = session.exec(select(db.Giveaway)).one()
        giveaway.categories_json = None
        video = session.exec(select(db.Video)).one()
        fetched = video.fetched_at
        session.add(giveaway)
        session.commit()
    db.create_db_and_tables(f'sqlite:///{tmp_path}/index.db')
    result = db.list_index(engine, category='books')
    assert result['total'] == 1
    with Session(engine) as session:
        assert session.exec(select(db.Video)).one().fetched_at == fetched


def test_categories_counts_and_sorts_hide_stale_and_expired(tmp_path):
    engine = db.create_db_and_tables(f'sqlite:///{tmp_path}/index.db')
    now = datetime.now(timezone.utc)
    for video_id, prize, confidence, published, delta in [
        ('book', 'Signed book', .9, '2026-01-01T00:00:00Z', 7),
        ('beauty', 'Skincare bundle', .8, '2026-02-01T00:00:00Z', 2),
        ('unknown', 'Signed book', .7, '2026-03-01T00:00:00Z', None),
        ('expired', 'Signed book', .99, '2026-04-01T00:00:00Z', -1),
        ('stale', 'Signed book', .99, '2026-05-01T00:00:00Z', 3),
    ]:
        db.save_giveaway_sync(engine, {'id': video_id, 'title': 'Giveaway', 'published_at': published,
            'description': 'Check source', 'channel_title': 'Creator'},
            {'prizes': prize, 'confidence': confidence, 'deadline': (now + timedelta(days=delta)).isoformat() if delta else None,
             'availability': 'active' if delta else 'unknown'})
    with Session(engine) as session:
        stale = session.exec(select(db.Video).where(db.Video.video_id == 'stale')).one()
        stale.fetched_at = now - timedelta(days=31)
        session.add(stale)
        session.commit()
    counts = {r['id']: r['count'] for r in db.category_counts(engine)}
    assert counts['books'] == 2 and counts['beauty'] == 1
    assert db.list_index(engine, category='books')['total'] == 2
    assert [r['video_id'] for r in db.list_index(engine, sort='confidence')['results']] == ['book', 'beauty', 'unknown']
    assert [r['video_id'] for r in db.list_index(engine, sort='newest')['results']] == ['unknown', 'beauty', 'book']
    assert [r['video_id'] for r in db.list_index(engine, sort='deadline')['results']] == ['beauty', 'book', 'unknown']
    assert db.list_index(engine)['results'][0]['channel_title'] == 'Creator'
    assert db.index_stats(engine)['indexed_videos'] == 4
    assert db.index_stats(engine)['giveaways'] == 3
