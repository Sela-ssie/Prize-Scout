"""Synthetic examples for an isolated preview; never mixed with a real index."""
from datetime import datetime, timedelta, timezone

from . import db
from .extractor import extract, giveaway_payload

DEMO_PRIZES = [
    ('Signed books for your next reading weekend', 'a signed book bundle'),
    ('Refresh your skincare shelf', 'a skincare bundle'),
    ('A new look for the season', 'a hoodie and a handbag'),
    ('Make something delicious', 'a cookware set'),
    ('A treat for chocolate lovers', 'a chocolate hamper'),
    ('Plan a weekend away', 'a weekend getaway'),
    ('Get ready for the trails', 'a camping tent'),
    ('Create your next masterpiece', 'an art supplies bundle'),
    ('Something for your four-legged friend', 'a dog toy bundle'),
    ('A ride for your next adventure', 'a bicycle'),
    ('Choose your own prize', 'a $100 gift card'),
    ('Capture your next adventure', 'a camera'),
    ('Your next game night', 'a Nintendo Switch'),
    ('Fresh stories for your shelf', 'a novel'),
    ('A little more color', 'a makeup set'),
    ('Coffee at home', 'an espresso machine'),
    ('A night of live music', 'concert tickets'),
    ('Supplies for a new project', 'a craft kit'),
]


def seed_demo(engine, now=None):
    now = now or datetime.now(timezone.utc)
    for i, (title, prize) in enumerate(DEMO_PRIZES):
        deadline = (now + timedelta(days=i + 3)).strftime('%B %d, %Y')
        description = (f'We are hosting a giveaway! Win {prize}. '
                       'To enter, subscribe and comment with your favorite idea. '
                       f'Entries close on {deadline}. '
                       'This is a fictional example for the PrizeScout demo, with no real prize or entry.')
        if i in (0, 13):
            description += ' Fictional shared entry link: https://gleam.io/Demo1/book-preview'
        analysis = extract(description, title=title + ' giveaway', now=now)
        db.save_giveaway_sync(engine, {'id': f'demo-{i:02d}', 'title': title,
            'channel_id': f'demo-creator-{i % 6}', 'channel_title': 'Example creator · fictional',
            'description': description, 'published_at': (now - timedelta(days=i)).isoformat()},
            giveaway_payload(analysis), analysis=analysis)
    return len(DEMO_PRIZES)
