from src.evidence import build_evidence
from src.extractor import find_deadline
from datetime import datetime, timezone


def test_empty_fields():
    assert build_evidence(None, None, {}) == {"prizes": [], "deadline": [], "howto": []}


def test_prize_exact_name_title_and_word_boundaries():
    evidence = build_evidence("Win a PLAYSTATION 5!", "cashmere", {
        "prizes_structured": [{"item": "PlayStation 5", "value_usd": 500}, {"item": "cash", "value_usd": 100}],
    })
    assert evidence["prizes"] == [{"source": "title", "start": 6, "end": 19,
                                    "quote": "PLAYSTATION 5", "value": "PlayStation 5"}]


def test_howto_preserves_whitespace_and_separate_source_segments():
    text = "Intro.\n  Subscribe\t and comment!\nSponsor details.\nFollow  us to enter."
    evidence = build_evidence("", text, {"howto": "Subscribe and comment! Follow us to enter."})
    assert [s["quote"] for s in evidence["howto"]] == ["Subscribe\t and comment!", "Follow  us to enter."]
    assert all(s["quote"] == text[s["start"]:s["end"]] for s in evidence["howto"])


def test_repeated_phrases_are_not_duplicated():
    text = "Subscribe to enter.\nSubscribe to enter."
    spans = build_evidence("", text, {"howto": "Subscribe to enter."})["howto"]
    assert len(spans) == 1
    assert spans[0]["start"] == 0


def test_fabricated_or_truncated_howto_is_unsupported():
    assert not build_evidence("", "Subscribe and comment to enter.", {"howto": "Subscribe and comment"})["howto"]


def test_full_date_is_supported_without_publication():
    text = "Entries close October 31, 2026."
    spans = build_evidence("", text, {"deadline": "2026-10-31T23:59:59+00:00"})["deadline"]
    assert len(spans) == 1
    assert spans[0]["quote"] == text


def test_relative_deadline_requires_publication_and_matches_extractor():
    publication = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    for text in ("Entries close tomorrow.", "Giveaway ends October 31."):
        target = find_deadline(text, now=publication, published_at=publication).isoformat()
        assert not build_evidence("", text, {"deadline": target})["deadline"]
        assert build_evidence("", text, {"deadline": target}, publication)["deadline"][0]["quote"] == text


def test_unrelated_and_ambiguous_deadlines_are_unsupported():
    for text in ("Sponsor sale ends October 31, 2026.",
                 "Uploaded October 31, 2026.",
                 "Entries close October 31, 2026 or November 1, 2026.",
                 "Ends October 31, 2026; closes November 1, 2026."):
        assert not build_evidence("", text, {"deadline": "2026-10-31T23:59:59+00:00"})["deadline"]


def test_ambiguous_relative_cutoffs_and_title_dates_are_unsupported():
    publication = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    text = "Entries close tomorrow or next week."
    target = find_deadline(text, now=publication, published_at=publication).isoformat()
    assert not build_evidence("", text, {"deadline": target}, publication)["deadline"]
    assert not build_evidence("Ends October 31, 2026.", "", {
        "deadline": "2026-10-31T23:59:59+00:00"})["deadline"]
