"""Original calendar qualifiers must survive parsing before availability is supported."""
from datetime import datetime, timezone

import pytest

from src.availability import assess_availability
from src.evidence import build_evidence
from src.extractor import find_deadline

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def assess(text, published=NOW, *, now=NOW):
    deadline = find_deadline(text, now=now, published_at=published)
    assert deadline is not None
    evidence = build_evidence('', text, {'deadline': deadline.isoformat()}, published_at=published)
    # Exercise the extra availability validation, not a missing evidence fallback.
    assert len(evidence['deadline']) == 1
    return deadline, assess_availability(description=text, deadline=deadline,
        published_at=published, source_fetched_at=now, now=now, evidence=evidence)


def test_missing_year_does_not_silently_roll_old_cutoff_to_next_year():
    deadline, result = assess('Make sure to get your entry in before the deadline on Tuesday, October 2nd.')
    assert deadline.year == 2027
    assert result['state'] == 'unconfirmed'


def test_missing_year_rollover_with_matching_weekday_still_requires_ninety_day_horizon():
    _, result = assess('Entries close Saturday, October 2nd.')
    assert result['state'] == 'unconfirmed'


def test_nearby_december_to_january_rollover_is_supported():
    published = datetime(2026, 12, 20, 12, tzinfo=timezone.utc)
    deadline, result = assess('Entries close Saturday, January 2nd.', published, now=published)
    assert deadline.year == 2027
    assert result['discoverable'] is True


def test_explicit_following_year_is_supported_when_weekday_matches():
    _, result = assess('Entries close Saturday, October 2, 2027.')
    assert result['discoverable'] is True


@pytest.mark.parametrize('text', ['Entries close Tuesday, October 25, 2026.',
    'Entries close Mon, October 25, 2026.'])
def test_inconsistent_weekday_is_unconfirmed(text):
    assert assess(text)[1]['state'] == 'unconfirmed'


def test_dropped_aest_does_not_become_a_utc_availability_claim():
    deadline, result = assess('📅 Entries close 25 October 2026 at 11:59 PM AEST.')
    assert deadline.hour == 23  # Current extractor drops this unsupported zone.
    assert result['state'] == 'unconfirmed'


def test_supported_timezone_weekday_uses_source_calendar_not_utc_day():
    deadline, result = assess('Entries close Sunday, October 25, 2026 at 11:59 PM EST.')
    assert deadline.astimezone(timezone.utc).day == 26
    assert result['discoverable'] is True


def test_wrong_source_weekday_remains_unconfirmed_across_utc_midnight():
    _, result = assess('Entries close Monday, October 25, 2026 at 11:59 PM EST.')
    assert result['state'] == 'unconfirmed'


@pytest.mark.parametrize('date', ['10/11/2026', '11-10-2026'])
def test_ambiguous_day_month_numeric_date_is_unconfirmed(date):
    assert assess(f'Entries close {date}.')[1]['state'] == 'unconfirmed'


def test_iso_year_first_date_is_supported():
    assert assess('Entries close 2026-10-25.')[1]['discoverable'] is True


def test_unambiguous_numeric_day_is_supported():
    assert assess('Entries close 25/10/2026.')[1]['discoverable'] is True


@pytest.mark.parametrize('clock', ['11:59 PM', '11:59 PM sharp'])
def test_clock_without_timezone_does_not_mistake_meridiem_for_zone(clock):
    assert assess(f'Entries close October 25, 2026 at {clock}.')[1]['discoverable'] is True
