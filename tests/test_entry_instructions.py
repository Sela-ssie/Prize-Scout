"""Entry fields require source context, not generic channel engagement requests."""
import pytest

from src.extractor import find_howto


@pytest.mark.parametrize('text', [
    'Be sure to LIKE, SUBSCRIBE, and FOLLOW on all social media!',
    'Subscribe and comment for more reviews.',
    'Giveaway! Win a camera. Subscribe for more videos and follow me on Instagram.',
    'Giveaway! Win a camera.\n\nSubscribe and comment below.',
    'How to enter:\nSubscribe and comment.\n\nFollow us on all social media!',
    'Reviewing an entry level camera. Subscribe and comment with your questions.',
])
def test_unrelated_engagement_is_not_an_entry_requirement(text):
    result = find_howto(text)
    if text.startswith('How to enter:'):
        assert result == 'Subscribe and comment.'
    else:
        assert result is None


@pytest.mark.parametrize('text,expected', [
    ('To enter, subscribe and comment below.', 'To enter, subscribe and comment below.'),
    ('Follow me on Instagram to enter this giveaway.', 'Follow me on Instagram to enter this giveaway.'),
    ('How to enter:\n1. Subscribe to the channel\n2. Comment your favorite color',
     'Subscribe to the channel Comment your favorite color'),
    ('Giveaway rules:\nSubscribe and comment below.', 'Subscribe and comment below.'),
    ('Giveaway! Win a camera. Subscribe and comment below.', 'Subscribe and comment below.'),
    ('Enter here: https://gleam.io/Ab123/example', 'Enter here: https://gleam.io/Ab123/example'),
    ('Enter via the form linked below.', 'Enter via the form linked below.'),
    ('Participate by subscribing and commenting your favorite video.',
     'Participate by subscribing and commenting your favorite video.'),
])
def test_explicit_or_local_giveaway_context_preserves_entry_steps(text, expected):
    assert find_howto(text) == expected


def test_an_unrelated_segment_ends_the_instruction_section():
    assert find_howto('How to enter:\nSubscribe and comment.\nSponsor product links below.\nShare our videos for more content.') == 'Subscribe and comment.'
