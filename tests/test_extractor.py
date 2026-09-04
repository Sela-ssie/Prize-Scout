from src.extractor import extract_giveaways_from_description


def test_happy_path():
    text = (
        "We're doing a giveaway! Win a Nintendo Switch and 100$ Amazon gift card. "
        "To enter, subscribe, like, and comment. Ends September 30, 2025."
    )
    res = extract_giveaways_from_description(text)
    assert res is not None
    assert 'Nintendo' in (res['prizes'] or '') or 'win' in (res['prizes'] or '')
    assert '2025-09-30' in (res['deadline'] or '')
    assert 'subscribe' in (res['howto'] or '').lower()


def test_no_giveaway():
    text = "This is a regular video description about coding tutorials and has no giveaways."
    res = extract_giveaways_from_description(text)
    assert res is None


def test_dateparser_behavior():
    # dateparser is a hard requirement, not an optional extra. This test used to skip
    # itself when it was missing, which is how the missing dependency went unnoticed.
    text = (
        "Enter to win a PlayStation 5! Subscribe and comment to enter. "
        "The giveaway ends on October 15, 2025."
    )
    res = extract_giveaways_from_description(text)
    assert res is not None
    assert res.get('deadline') is not None


def test_prize_amount_extraction():
    text = "Giveaway: Win $500 cash and a PlayStation 5. To enter, subscribe and comment."
    res = extract_giveaways_from_description(text)
    assert res is not None
    assert '$500' in (res['prizes'] or '') or '500' in (res['prizes'] or '')
