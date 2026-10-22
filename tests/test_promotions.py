"""Synthetic source claims, not live code-redemption or merchant verification."""
from datetime import datetime, timezone

import pytest

from src.promotions import extract_promotions

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def promotions(text, **kwargs):
    return extract_promotions(text, now=NOW, **kwargs)


def codes(rows):
    return [row for row in rows if row['kind'] == 'promo_code']


@pytest.mark.parametrize('text,code,benefit', [
    ('Use code MixedCase20 for 20% off at checkout.', 'MixedCase20', '20% off'),
    ('Coupon code: New-Buyer_10 gives $10 off your order.', 'New-Buyer_10', '$10 off'),
    ('Save 15% with promo code FALL15.', 'FALL15', 'Save 15%'),
    ('Discount code X: free shipping.', 'X', 'free shipping'),
    ('Apply the code HELLO for a 12.5% discount.', 'HELLO', '12.5% discount'),
    ('Promotional code "QuietSale".', 'QuietSale', None),
])
def test_explicit_codes_preserve_case_exact_benefit_and_unverified_status(text, code, benefit):
    result = codes(promotions(text))
    assert len(result) == 1
    candidate = result[0]
    assert candidate['code'] == code
    assert candidate['benefit_claim'] == benefit
    assert candidate['availability'] == 'unverified'
    assert candidate['last_checked_at'] is None
    assert candidate['evidence']['quote'] == text[candidate['evidence']['start']:candidate['evidence']['end']]
    assert candidate['evidence']['source'] == 'description'
    assert code in candidate['evidence']['quote']
    if benefit:
        assert benefit in candidate['evidence']['quote']


def test_multiple_codes_do_not_borrow_benefits_dates_or_merchants():
    text = ('Coupon code FIRST gives 10% off, expires 2026-10-01 at https://shop.example.com/one; '
            'Use code SECOND for 20% off at https://shop.other.com/two; '
            'Promo code THIRD expires 2026-12-01.')
    first, second, third = codes(promotions(text))
    assert first['code'] == 'FIRST' and first['availability'] == 'expired'
    assert first['merchant_host'] == 'shop.example.com'
    assert second['code'] == 'SECOND' and second['benefit_claim'] == '20% off'
    assert second['expires_at'] is None and second['availability'] == 'unverified'
    assert second['merchant_host'] == 'shop.other.com'
    assert third['code'] == 'THIRD' and third['benefit_claim'] is None
    assert third['url'] is None and third['expires_at'].startswith('2026-12-01')


def test_multiple_codes_in_same_clause_keep_following_dates_separate():
    result = codes(promotions('Promo code EARLY 10% off expires 2026-10-01 and promo code LATER 20% off expires 2026-12-01'))
    assert [(r['code'], r['availability'], r['benefit_claim']) for r in result] == [
        ('EARLY', 'expired', '10% off'), ('LATER', 'unverified', '20% off')]
    assert result[0]['expires_at'] != result[1]['expires_at']


def test_unrelated_date_and_title_do_not_supply_code_context():
    result = codes(promotions('Video uploaded 2020-01-01. Promo code FRESH offers 20% off.', title='Code expired 2020-02-01'))
    assert result[0]['expires_at'] is None
    assert result[0]['availability'] == 'unverified'
    assert promotions('Use code VIDEO to open the door.', title='25% off discount code') == []


def test_relative_dates_are_anchored_to_publication_and_not_reprocessing():
    text = 'Coupon code TOM gives 20% off, starts today and expires tomorrow.'
    result = codes(promotions(text, published_at='2026-10-10T09:00:00+00:00'))[0]
    assert result['starts_at'].startswith('2026-10-10')
    assert result['expires_at'].startswith('2026-10-11')
    assert result['availability'] == 'expired'
    no_anchor = codes(promotions(text))[0]
    assert no_anchor['starts_at'] is no_anchor['expires_at'] is None


def test_ambiguous_expiry_claims_remain_unknown():
    result = codes(promotions('Promo code MAYBE offers 20% off, expires 2026-01-01 or expires 2026-12-31.'))[0]
    assert result['expires_at'] is None and result['availability'] == 'unverified'


@pytest.mark.parametrize('text', [
    'Use code DOOR to enter the giveaway.',
    'The game code opens level 2.',
    'The programming code shows how to calculate a 20% discount.',
    'Use code VALUE in this Python source code to compute 20% off.',
    'Enter verification code 123456 to receive a 10% discount.',
    'An entry code gives you a chance to win.',
    'Promo codes are listed elsewhere.',
    'The coupon code is not available.',
    'Use code here to continue.',
])
def test_nonpromotional_codes_and_placeholders_are_excluded(text):
    assert codes(promotions(text)) == []


@pytest.mark.parametrize('url', [
    'https://www.amazon.com/dp/123?tag=reviewer-20',
    'https://amzn.to/abc123',
    'https://www.shareasale.com/r.cfm?b=123',
    'https://click.linksynergy.com/deeplink?id=abc',
    'https://shop.example.com/item?affiliate_id=creator',
])
def test_known_affiliate_signals_are_links_and_never_discount_codes(url):
    result = promotions(url)
    assert len(result) == 1 and result[0]['kind'] == 'affiliate_link'
    assert result[0]['code'] is None and result[0]['benefit_claim'] is None
    assert result[0]['url'] == url
    assert result[0]['availability'] == 'unverified'


def test_affiliate_disclosure_is_local_and_does_not_imply_discount():
    text = 'Affiliate link: https://merchant.example.com/product.\nOther products: https://unrelated.example.com/item'
    result = promotions(text)
    assert len(result) == 1
    assert result[0]['merchant_host'] == 'merchant.example.com'
    assert result[0]['benefit_claim'] is None
    assert result[0]['evidence']['quote'] == text[:text.index('\n')]
    assert promotions('This is not an affiliate link: https://shop.example.com/item') == []


@pytest.mark.parametrize('url', [
    'https://shop.example.com/item?utm_source=youtube&utm_campaign=summer',
    'https://shop.example.com/item?source=channel&ref=creator',
    'https://amazon.evil.example.com/item?tag=notamazon',
    'https://www.amazon.com/item',
])
def test_plain_tracking_and_merchant_links_are_not_affiliate_promotions(url):
    assert promotions(url) == []


@pytest.mark.parametrize('url', [
    'http://localhost/item', 'http://127.0.0.1/item', 'http://10.0.0.1/item',
    'http://169.254.169.254/latest', 'http://[::1]/item', 'http://[fc00::1]/item',
    'http://[::ffff:127.0.0.1]/item', 'https://person:secret@shop.example.com/item',
    'http://localhost./item', 'http://printer.local/item', 'http://127.1/item',
    'http://2130706433/item', 'http://0x7f000001/item', 'file:///private/file',
    'javascript:https://shop.example.com/item', 'https://shop.example.com:invalid/item',
])
def test_unsafe_urls_are_never_exposed_as_candidate_urls(url):
    assert promotions('Affiliate link: ' + url) == []
    result = codes(promotions('Promo code SAFE gives 10% off at ' + url))[0]
    assert result['url'] is None and result['merchant_host'] is None


def test_multiple_merchants_are_not_guessed_for_a_code():
    result = codes(promotions('Coupon code SHARED gives 10% off: https://one.example.com and https://two.example.com'))[0]
    assert result['url'] is None and result['merchant_host'] is None


def test_late_code_in_long_description_is_retained_with_exact_bounded_quote():
    text = ('Ordinary channel information.\n' * 3000) + ('filler ' * 1000) + 'Use code LastOne for 35% off.'
    candidate = codes(promotions(text))[0]
    evidence = candidate['evidence']
    assert candidate['code'] == 'LastOne'
    assert len(evidence['quote']) < 500
    assert evidence['quote'] == text[evidence['start']:evidence['end']]
    assert '35% off' in evidence['quote']


def test_aware_clock_required_and_objects_are_json_compatible():
    import json
    with pytest.raises(ValueError, match='timezone-aware'):
        extract_promotions('Promo code TEST gives 10% off.', now=datetime(2026, 10, 16))
    json.dumps(promotions('Promo code TEST gives 10% off.'))


def test_adjacent_affiliate_heading_supports_only_the_next_source_line():
    text = 'Affiliate links:\nhttps://shop.example.com/item\nhttps://other.example.com/item'
    result = promotions(text)
    assert len(result) == 1
    assert result[0]['evidence']['quote'].startswith('Affiliate links:\n')
    assert result[0]['url'] == 'https://shop.example.com/item'


def test_hexadecimal_looking_public_domain_is_not_an_encoded_ip():
    result = promotions('Affiliate link: https://cafe.de/item')
    assert len(result) == 1 and result[0]['merchant_host'] == 'cafe.de'


def test_same_day_date_only_expiry_is_not_claimed_expired_midday():
    result = codes(promotions('Promo code TODAY gives 10% off, expires 2026-10-16.'))[0]
    assert result['availability'] == 'unverified'
    assert result['expires_at'].startswith('2026-10-16T23:59:59')


def test_affiliate_multi_link_clause_does_not_cross_bind_expiry_or_benefit():
    text = ('Affiliate links: https://one.example.com expires 2020-01-01 and '
            'https://two.example.com offers 20% off.')
    result = promotions(text)
    assert [row['url'] for row in result] == ['https://one.example.com', 'https://two.example.com']
    for row in result:
        assert row['kind'] == 'affiliate_link'
        assert row['benefit_claim'] is None
        assert row['starts_at'] is None and row['expires_at'] is None
        assert row['availability'] == 'unverified'
        assert row['evidence']['quote'] == text[row['evidence']['start']:row['evidence']['end']]


def test_separate_affiliate_link_clauses_retain_their_own_expiry_claims():
    text = ('Affiliate link: https://one.example.com expires 2020-01-01; '
            'Affiliate link: https://two.example.com offers 20% off, expires 2099-01-01.')
    first, second = promotions(text)
    assert first['availability'] == 'expired' and first['benefit_claim'] is None
    assert second['availability'] == 'unverified' and second['benefit_claim'] == '20% off'
    assert second['expires_at'].startswith('2099-01-01')
