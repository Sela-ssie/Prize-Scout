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


# --------------------------------------------------------------------------------------
# Title as a second input (2.2.0)
# --------------------------------------------------------------------------------------

def test_title_admits_a_giveaway_the_description_never_names():
    """The motivating case: the creator announced it in the title and the description
    only carries the mechanics. Description-only, this was rejected outright."""
    text = (
        "All the details are in the video. To enter, subscribe to the channel, like this "
        "video and drop a comment telling me which colour you want. Winner picked live."
    )
    assert extract(text, now=NOW)["rejected_because"] == "no_giveaway_indicator"

    res = extract(text, title="$500 PS5 GIVEAWAY!", now=NOW)
    assert res["is_giveaway"]
    assert res["title_only"]
    assert any(p["value_usd"] == 500.0 for p in res["prizes"])


def test_title_does_not_move_the_description_normalised_features():
    """short_text, hashtag_ratio and is_tag_dump are ratios over the description. If the
    title were concatenated into the text they would all shift for every video, which is
    why title signals are their own features."""
    text = (
        "We're doing a giveaway! Win a Nintendo Switch. To enter, subscribe, like, and "
        "comment. Ends September 30, 2025."
    )
    plain = extract(text, now=NOW)["features"]
    titled = extract(text, title="#giveaway #free #win #shorts #viral #trending", now=NOW)["features"]
    for shared in ("short_text", "hashtag_ratio", "is_tag_dump"):
        assert plain[shared] == titled[shared], shared
    assert titled["title_strong"] == 1.0


def test_closed_giveaway_in_the_title_is_rejected():
    """Widening the gate to titles means headline-shaped closures have to be caught.
    'Winner Announced' carries no auxiliary verb, so the original patterns missed it."""
    text = (
        "Thanks to everyone who entered this month. Subscribe, like and comment to be "
        "part of the next one. Full details and the draw are in the video above."
    )
    for title in (
        "GIVEAWAY WINNER ANNOUNCED! Soundcore Q20i Headphones",
        "August 2026 Giveaway Winner Announcement",
        "100 Subscriber Giveaway ENDED! Winner revealed",
    ):
        assert extract(text, title=title, now=NOW)["rejected_because"] == "already_closed", title


def test_a_giveaway_still_announcing_its_winner_later_is_not_closed():
    """'winners announced soon' is an open giveaway; only the past tense closes one."""
    text = (
        "Giveaway time! Win a Nintendo Switch. To enter, subscribe, like and comment "
        "below. Winners announced soon, so keep an eye on the community tab."
    )
    assert extract(text, now=NOW)["is_giveaway"]


def test_title_contributes_only_recognised_items_never_money_labels():
    """_money_label names an unclaimed amount from the words after it. That reads prose
    correctly but turned '$1200 PC GIVEAWAY!' into the prize 'PC GIVEAWAY'."""
    text = (
        "Chris answers your questions on the giveaway. Find our monthly giveaway on the "
        "site, and subscribe to the channel so you do not miss the next one."
    )
    res = extract(text, title="$1200 PC GIVEAWAY! !SITE HotDropFPS.com", now=NOW)
    assert not any("giveaway" in p["item"].lower() for p in res["prizes"]), res["prizes"]


def test_clickbait_title_without_a_giveaway_word_is_not_mined_for_prizes():
    """'$5000 Gaming PC Build' names no prize. Only a title that has actually announced a
    giveaway gets mined."""
    text = "Full parts list and benchmarks below. Timestamps are in the pinned comment."
    res = extract(text, title="$5000 Gaming PC Build 2026", now=NOW)
    assert not res["is_giveaway"]
    assert res["prizes"] == []


def test_expired_cutoff_is_preserved_but_not_admitted():
    text = 'Giveaway! Win a Nintendo Switch. Subscribe and comment to enter. Ends August 20, 2025.'
    result = extract(text, now=NOW)
    assert not result['is_giveaway']
    assert result['availability'] == 'expired'
    assert result['rejected_because'] == 'expired'
    assert result['deadline'].startswith('2025-08-20')


def test_availability_distinguishes_unknown_from_future_cutoff():
    text = 'Giveaway! Win a Nintendo Switch. Subscribe and comment to enter.'
    assert extract(text, now=NOW)['availability'] == 'unknown'
    assert extract(text + ' Ends October 3, 2025.', now=NOW)['availability'] == 'active'


def test_draw_date_and_live_today_are_not_entry_cutoffs():
    from src.extractor import find_deadline
    assert find_deadline('Winners drawn on October 3, 2025.', now=NOW) is None
    assert find_deadline('Free fire live today, live giveaway, free fire live now.', now=NOW) is None


def test_relative_cutoff_anchors_to_publication_when_reprocessed():
    text = 'Giveaway! Win a Nintendo Switch. Subscribe and comment to enter. Entries close tomorrow.'
    result = extract(text, published_at='2025-08-20T10:00:00Z', now=NOW)
    assert result['deadline'].startswith('2025-08-21')
    assert result['availability'] == 'expired'


def test_date_only_cutoff_lasts_through_same_day():
    text = 'Giveaway! Win a Nintendo Switch. Subscribe and comment to enter. Ends September 1, 2025.'
    result = extract(text, published_at='2025-09-01T01:00:00Z', now=NOW.replace(hour=12))
    assert result['availability'] == 'active'


def test_explicit_timezone_cutoff_is_respected():
    text = 'Giveaway! Win a Nintendo Switch. Subscribe and comment to enter. Ends September 1, 2025 at 9:00 AM PDT.'
    result = extract(text, now=NOW.replace(hour=17))
    assert result['deadline'].startswith('2025-09-01T16:00:00')
    assert result['availability'] == 'expired'


def test_generic_subscribe_request_is_not_an_entry_offer():
    text = 'Welcome to our gaming channel! Subscribe for more livestreams and reactions. Free Fire live today.'
    result = extract(text, title='LIVE REACTION | GIVEAWAY', now=NOW)
    assert not result['is_giveaway']
    assert result['rejected_because'] == 'unsupported_giveaway_mention'


def test_closed_result_preserves_deadline():
    from src.extractor import extract
    result = extract('Giveaway! Win a Nintendo Switch. The giveaway ended on August 20, 2025. Thanks for entering.',
                     now=datetime(2025, 9, 1, tzinfo=timezone.utc))
    assert result['availability'] == 'expired'
    assert result['deadline'].startswith('2025-08-20')
