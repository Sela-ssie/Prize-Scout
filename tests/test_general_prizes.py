"""Handwritten coverage cases, not an estimate of real-world extraction precision."""
from datetime import datetime, timezone

import pytest

from src.extractor import analyse, extract, extract_prizes

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)

# Named goods and experiences across the catalog, including more than one item in
# categories with very different nouns. Assert the actual extracted span, not only
# admission: unknown prizes used to pass on strong entry instructions alone.
ITEMS = [
    "signed book", "cookbook", "boxed set of books",
    "skincare kit", "eyeshadow palette", "perfume",
    "handbag", "sneakers", "necklace",
    "air fryer", "stand mixer", "cookware set", "vacuum cleaner",
    "TV stand", "coffee table", "chair", "desk",
    "snack hamper", "chocolate box", "restaurant voucher",
    "trip to Paris", "weekend getaway", "hotel stay", "concert tickets", "spa day",
    "camping tent", "fishing rod", "tennis racket", "yoga mat", "hiking boots",
    "bicycle", "bicycles", "board game", "board games",
    "sewing machine", "art supplies", "crochet kit", "yarn bundle",
    "dog toys", "cat food", "pet supplies", "cat tree",
    "car detailing kit", "dash cam", "tires", "tool set",
    "cash",
]


@pytest.mark.parametrize("item", ITEMS)
def test_general_prize_offer_extracts_named_item(item):
    result = extract(
        f"Giveaway! Enter to win a {item}. To enter, subscribe and comment below. "
        "Entries close November 10, 2026.", now=NOW,
    )
    assert result["is_giveaway"], result
    assert any(p["item"].lower() == item.lower() for p in result["prizes"]), result["prizes"]
    assert result["availability"] == "active"


@pytest.mark.parametrize("item", ["signed book", "skincare kit", "air fryer", "hotel stay", "dog toys"])
def test_nontech_title_prizes_keep_description_separate(item):
    text = "To enter, subscribe and comment with your favourite colour. Full details in the video."
    plain = analyse(text, now=NOW)
    result = extract(text, title=f"{item} GIVEAWAY!", now=NOW)
    assert result["is_giveaway"]
    assert result["title_only"]
    assert any(p["item"].lower() == item for p in result["prizes"])
    for feature in ("short_text", "hashtag_ratio", "is_tag_dump"):
        assert result["features"][feature] == plain["features"][feature]


@pytest.mark.parametrize("item", ITEMS)
def test_review_and_affiliate_offer_are_not_prize_offers(item):
    result = extract(
        f"Today I review this {item} and compare the prices. Shop through my affiliate link "
        "for a discount. Subscribe and comment with your questions for the next review.",
        title=f"{item} review and shopping guide", now=NOW,
    )
    assert not result["is_giveaway"]
    assert result["prizes"] == []


@pytest.mark.parametrize("text,title", [
    ("Book giveaway scam warning: impersonating accounts ask for shipping fees. Stay alert and report them.", ""),
    ("Giveaway! Win a skincare kit. Subscribe and comment to enter. The giveaway has ended.", ""),
    ("My honest air fryer review. Affiliate links below; subscribe for more recipes and kitchen reviews.", "Giveaway winner announced"),
    ("Looking back at last year's pet supplies giveaway. This creator shared their giveaway footage for our reaction.", "Pet giveaway reaction"),
    ("Craft tutorial showing how to win at a local art contest. Commentary and analysis of their contest results.", ""),
    ("A winter cabin vlog with chocolate and coffee. Watching the window while the dog plays by the fire.", ""),
])
def test_general_category_hard_negatives(text, title):
    assert not extract(text, title=title, now=NOW)["is_giveaway"]


def test_allowlist_does_not_extract_arbitrary_prize_prose():
    assert extract_prizes("Enter to win a mysterious wonderful surprise from our sponsor.") == []


@pytest.mark.parametrize("word", ["booking", "notebook", "tentative", "scar", "cartoon", "catfood", "headphonesque", "bicyclesque", "board gamesmanship"])
def test_named_items_respect_word_boundaries(word):
    assert extract_prizes(f"Giveaway commentary about {word} today.") == []


def test_money_stays_with_nontech_prize_it_values():
    prizes = extract_prizes("Win a $100 skincare kit and an air fryer worth $250.")
    assert {p["item"]: p["value_usd"] for p in prizes} == {"skincare kit": 100.0, "air fryer": 250.0}


def test_remote_money_does_not_become_an_item_value():
    prizes = extract_prizes(
        "Win $500 and receive another wonderful surprise selected especially for our "
        "loyal viewers this month: an air fryer."
    )
    assert {p["item"]: p["value_usd"] for p in prizes} == {"cash": 500.0, "air fryer": None}


def test_furniture_compounds_do_not_become_electronics():
    prizes = extract_prizes("Win a TV stand and a coffee table in our giveaway.")
    assert [p["item"].lower() for p in prizes] == ["tv stand", "coffee table"]


def test_furniture_does_not_hide_a_separate_television_prize():
    prizes = extract_prizes("Win a TV stand and a TV in our giveaway.")
    assert [p["item"].lower() for p in prizes] == ["tv stand", "tv"]


@pytest.mark.parametrize("word", ["chairperson", "desktop", "coffee tabletop", "TV standby"])
def test_furniture_words_respect_boundaries(word):
    prizes = extract_prizes(f"Giveaway discussion about {word} today.")
    assert not any(p["item"].lower() in {"chair", "desk", "coffee table", "tv stand"} for p in prizes)


@pytest.mark.parametrize("item", ["air fryer", "signed book", "dog toys"])
def test_review_with_giveaway_mention_stays_below_threshold(item):
    result = extract(
        f"Today I review a {item} giveaway from a brand. Affiliate links below. "
        "Subscribe and comment for more reviews.", now=NOW,
    )
    assert not result["is_giveaway"], result
