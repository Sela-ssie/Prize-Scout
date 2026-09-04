from datetime import datetime, timezone

from src.extractor import extract, extract_giveaways_from_description

# Hardcoded dates in fixtures expire. Pin the clock so these tests do not rot.
NOW = datetime(2025, 9, 1, tzinfo=timezone.utc)


def test_happy_path():
    text = (
        "We're doing a giveaway! Win a Nintendo Switch and 100$ Amazon gift card. "
        "To enter, subscribe, like, and comment. Ends September 30, 2025."
    )
    res = extract_giveaways_from_description(text, now=NOW)
    assert res is not None
    assert 'Nintendo' in (res['prizes'] or '')
    assert (res['deadline'] or '').startswith('2025-09-30')
    assert 'subscribe' in (res['howto'] or '').lower()


def test_no_giveaway():
    text = "This is a regular video description about coding tutorials and has no giveaways."
    res = extract_giveaways_from_description(text, now=NOW)
    assert res is None


def test_dateparser_behavior():
    # dateparser is a hard requirement, not an optional extra. This test used to skip
    # itself when the dependency was missing, which is how its absence went unnoticed.
    text = (
        "Enter to win a PlayStation 5! Subscribe and comment to enter. "
        "The giveaway ends on October 15, 2025."
    )
    res = extract_giveaways_from_description(text, now=NOW)
    assert res is not None
    assert (res['deadline'] or '').startswith('2025-10-15')


def test_prize_amount_extraction():
    text = (
        "Giveaway: Win $500 cash and a PlayStation 5. To enter, subscribe and comment "
        "on this video before the end of the month."
    )
    res = extract_giveaways_from_description(text, now=NOW)
    assert res is not None
    assert any(p['value_usd'] == 500.0 for p in res['prizes_structured'])
    assert any('PlayStation' in p['item'] for p in res['prizes_structured'])


def test_money_is_assigned_to_the_nearest_item_only():
    text = (
        "Huge giveaway! Win a Nintendo Switch and a $500 Amazon gift card. "
        "To enter, subscribe, like, and comment below."
    )
    res = extract(text, now=NOW)
    valued = {p['item']: p['value_usd'] for p in res['prizes']}
    assert any('gift card' in k.lower() and v == 500.0 for k, v in valued.items())
    assert all(v is None for k, v in valued.items() if 'Switch' in k)
