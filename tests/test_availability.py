import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.availability import assess_availability, availability_policy
from src.evidence import build_evidence

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
TEXT = 'Giveaway ends October 31, 2026.'
DEADLINE = '2026-10-31T23:59:59+00:00'


def assess(**changes):
    args = dict(description=TEXT, deadline=DEADLINE,
                evidence=build_evidence('', TEXT, {'deadline': DEADLINE}), source_fetched_at=NOW, now=NOW)
    args.update(changes)
    return assess_availability(**args)


def test_supported_future_entry_is_source_reported_not_verified():
    result = assess()
    assert result['state'] == 'source_reported_open'
    assert result['discoverable'] is True
    assert 'not independently verified' in result['label']
    assert result['recheck_due_at'] == (NOW + timedelta(hours=48)).isoformat()
    assert TEXT not in json.dumps(result)
    assert availability_policy()['deadline_comparison'] == 'strictly_future'


@pytest.mark.parametrize('field,value', [('quote', 'Forged giveaway ends October 31, 2026.'),
    ('start', 1), ('end', 3), ('start', True), ('source', 'unknown'), ('value', '2027-10-31T23:59:59Z')])
def test_forged_or_mismatched_evidence_is_unconfirmed(field, value):
    evidence = copy.deepcopy(build_evidence('', TEXT, {'deadline': DEADLINE}))
    evidence['deadline'][0][field] = value
    assert assess(evidence=evidence)['state'] == 'unconfirmed'


def test_valid_quote_cannot_assert_another_date_and_ambiguous_spans_fail():
    evidence = build_evidence('', TEXT, {'deadline': DEADLINE})
    evidence['deadline'][0]['value'] = '2027-10-31T23:59:59Z'
    assert assess(deadline='2027-10-31T23:59:59Z', evidence=evidence)['discoverable'] is False
    evidence = build_evidence('', TEXT, {'deadline': DEADLINE})
    evidence['deadline'] *= 2
    assert assess(evidence=evidence)['discoverable'] is False


@pytest.mark.parametrize('text', ['Sale ends October 31, 2026.', 'Promo code ends October 31, 2026.',
    'Giveaway winner stream ends October 31, 2026.', 'Giveaway upload deadline October 31, 2026.',
    'Giveaway ends October 31, 2026 and entries close November 2, 2026.'])
def test_unrelated_dates_or_multiple_cutoffs_do_not_establish_open_entries(text):
    forged = {'deadline': [dict(source='description', start=0, end=len(text), quote=text, value=DEADLINE)]}
    assert assess(description=text, evidence=forged)['state'] == 'unconfirmed'


@pytest.mark.parametrize('hours,state', [(47.999, 'source_reported_open'), (48, 'source_reported_open'),
    (48.001, 'needs_recheck'), (721, 'needs_recheck')])
def test_check_ttl_boundary(hours, state):
    assert assess(source_fetched_at=NOW - timedelta(hours=hours))['state'] == state


def test_fetch_clock_missing_future_and_sqlite_naive():
    assert assess(source_fetched_at=None)['state'] == 'needs_recheck'
    assert assess(source_fetched_at=NOW + timedelta(seconds=1))['state'] == 'unconfirmed'
    assert assess(source_fetched_at=NOW.replace(tzinfo=None))['state'] == 'source_reported_open'


def test_expiry_requires_support_and_stored_expiry_wins():
    assert assess(now=datetime(2026, 11, 1, tzinfo=timezone.utc))['state'] == 'expired'
    assert assess(now=datetime(2026, 11, 1, tzinfo=timezone.utc), evidence={})['state'] == 'unconfirmed'
    assert assess(stored_availability='expired', evidence={})['state'] == 'expired'
    assert assess(now=datetime.fromisoformat(DEADLINE))['state'] == 'expired'


def test_old_publication_and_missing_deadline_do_not_infer_expiry():
    assert assess(published_at='2001-01-01T00:00:00Z')['state'] != 'expired'
    assert assess(deadline=None, published_at='2001-01-01T00:00:00Z')['state'] == 'unconfirmed'
    assert assess(description='This giveaway is now closed. ' + TEXT)['state'] == 'closed'
    assert assess(evidence={}, stored_availability='active')['state'] == 'unconfirmed'


@pytest.mark.parametrize('closed_text', ['Entries have closed.', 'Giveaway is no longer open.', 'Contest has now ended.',
    'Giveaway winner announced', 'Winners have already been chosen.', 'Entries have been closed.',
    'Giveaway has finished.', 'We are no longer accepting entries.'])
def test_explicit_closed_entry_text_overrides_future_cutoff(closed_text):
    assert assess(description=closed_text + '\n' + TEXT)['state'] == 'closed'


@pytest.mark.parametrize('opening', ['Entries open October 25, 2026.',
    'Giveaway will start next week.', 'The entry period begins soon.', 'Contest starts October 1, 2026.',
    'Entries open today at 11 PM.'])
def test_separate_opening_claim_requires_review_even_with_future_cutoff(opening):
    text = opening + '\n' + TEXT
    result = assess(description=text, evidence=build_evidence('', text, {'deadline': DEADLINE}))
    assert result['state'] == 'unconfirmed'
    assert result['reasons'] == ['entry_opening_requires_review']


def test_explicit_current_opening_does_not_cross_bind_future_sale_opening():
    text = 'Entries open now. Sale starts October 25, 2026.\n' + TEXT
    assert assess(description=text, evidence=build_evidence('', text, {'deadline': DEADLINE}))['discoverable'] is True
