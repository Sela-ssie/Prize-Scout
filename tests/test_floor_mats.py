"""Development regressions from guided promotion review, not held-out accuracy."""
from datetime import datetime, timezone

import pytest

from src.categories import classify_prizes
from src.evidence import build_evidence
from src.extractor import extract, extract_prizes, giveaway_payload

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def test_title_offer_with_floor_mats_and_generic_footer_is_detected():
    title = 'GIVEAWAY: Enter to Win Free All-Weather Floor Mats from a sponsor!'
    description = ('This floor mats giveaway is featured in the video. Recommended floor mats for your car. Product links below. '
                   'Be sure to LIKE, SUBSCRIBE, and FOLLOW on all social media!')
    result = extract(description, title=title, now=NOW)
    assert result['is_giveaway']
    assert result['availability'] == 'unknown'
    assert result['deadline'] is None
    assert result['howto'] is None
    assert any('floor mats' in p['item'].lower() for p in result['prizes'])
    assert 'automotive' in classify_prizes(result['prizes'])
    evidence = build_evidence(title, description, giveaway_payload(result))
    assert evidence['prizes']
    for span in evidence['prizes']:
        source = description if span['source'] == 'description' else title
        assert source[span['start']:span['end']] == span['quote']


def test_short_title_offer_no_longer_gets_credit_for_a_generic_footer():
    result = extract('Recommended floor mats for your car. Product links below. '
                     'Be sure to LIKE, SUBSCRIBE, and FOLLOW on all social media!',
                     title='GIVEAWAY: Enter to Win Free All-Weather Floor Mats from a sponsor!', now=NOW)
    # Intentional precision/recall tradeoff: unsupported entry details must not
    # inflate confidence merely to keep this weak synthetic source above threshold.
    assert result['howto'] is None
    assert result['features']['has_howto'] == 0
    assert result['features']['howto_multi_step'] == 0
    assert result['rejected_because'] == 'below_threshold'


@pytest.mark.parametrize('text,title', [
    ('Today I review floor mats. Buy through my affiliate links and subscribe for more.', 'Floor mats review'),
    ('Today I review floor mats from a giveaway. Affiliate links below. Subscribe for more reviews.', 'Floor mats review'),
    ('Subscribe and follow for more car content. No prizes are being offered.', 'Not a floor mats giveaway'),
    ('Thank you to everyone who entered our floor mats giveaway. The giveaway has ended.', 'Floor mats giveaway'),
])
def test_floor_mats_reviews_negation_and_closed_offers_stay_rejected(text, title):
    assert not extract(text, title=title, now=NOW)['is_giveaway']


def test_floor_mats_name_has_word_boundaries_and_allows_hyphenation():
    assert extract_prizes('Win floor-mats in our giveaway.')[0]['item'] == 'floor-mats'
    assert not extract_prizes('A floor matshop and floor matting discussion.')
