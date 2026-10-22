"""Conservative source-reported availability; no independent verification claims."""
import re
from datetime import datetime, timedelta, timezone

from .evidence import build_evidence
from .extractor import CLOSED_RE

DEFAULT_MAX_CHECK_AGE_HOURS = 48
SOURCE_CACHE_MAX_AGE_DAYS = 30
DISCOVERABLE_STATE = 'source_reported_open'

_LABELS = {
    'source_reported_open': 'Source reports an open entry period; not independently verified',
    'expired': 'Entry period expired',
    'closed': 'Source reports entries closed',
    'unconfirmed': 'Availability unconfirmed',
    'needs_recheck': 'Source needs a recent availability check',
}
_CLOSED = re.compile(
    r'\b(?:giveaway|contest|sweepstakes|entries|entry\s+period)\s+(?:(?:is|has|have|are)\s+)?(?:been\s+)?'
    r'(?:now\s+)?(?:closed|ended|over|finished|concluded|no\s+longer\s+open)\b|\b(?:closed|ended)\s+(?:giveaway|contest|sweepstakes)\b', re.I)
_ENTRY = re.compile(r'\b(?:giveaway|contest|sweepstakes|entries|entry|enter)\b', re.I)
_UNRELATED = re.compile(
    r'\b(?:winners?|draw(?:ing)?|announc\w*|live\s*streams?|streams?|sales?|coupon\w*|'
    r'discounts?|promo\w*|affiliate\w*|upload\w*|publish\w*)\b', re.I)
_OPENING_CLAIM = re.compile(
    r'\b(?:entries|entry\s+period|giveaway|contest|sweepstakes)\s+(?:(?:will|shall)\s+)?'
    r'(?:open(?:s)?|start(?:s)?|begin(?:s)?)\b(?!\s+(?:now|currently)\b)', re.I)


def _datetime(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def availability_policy():
    """Describe the persisted-support flag and runtime SQL requirements.

    The flag is derived from full original evidence validation, never just a
    deadline. It must be invalidated whenever input or extraction changes.
    """
    return dict(discoverable_state=DISCOVERABLE_STATE,
        max_check_age_hours=DEFAULT_MAX_CHECK_AGE_HOURS, cache_max_age_days=SOURCE_CACHE_MAX_AGE_DAYS,
        deadline_comparison='strictly_future', checked_age_comparison='inclusive',
        requires_supported_deadline=True, requires_nonfuture_source_check=True,
        support_flag_policy='Set from source_reported_open or needs_recheck with a supported future deadline; recompute on source or extraction changes. Runtime requires future deadline, fetched_at <= now, fetched_at >= now - check age, and no related fresh closure report.')


# These fixed abbreviations are the explicit zones supported by the extractor.
# Others must abstain: reparsing alone cannot detect a silently dropped zone.
_ZONE_HOURS = dict(UTC=0, GMT=0, EST=-5, EDT=-4, CST=-6, CDT=-5,
                   MST=-7, MDT=-6, PST=-8, PDT=-7)
_WEEKDAYS = {name: index for index, names in enumerate(
    [('mon', 'monday'), ('tue', 'tues', 'tuesday'), ('wed', 'wednesday'),
     ('thu', 'thur', 'thurs', 'thursday'), ('fri', 'friday'),
     ('sat', 'saturday'), ('sun', 'sunday')]) for name in names}
_WEEKDAY_RE = re.compile(r'\b(' + '|'.join(sorted(_WEEKDAYS, key=len, reverse=True)) + r')\b', re.I)


def _sane_deadline_quote(quote, deadline, published_at):
    """Reject dates whose original calendar/clock qualifiers were lost in parsing."""
    # Day/month order is ambiguous when both fields could be a month. ISO's
    # year-first order is explicit and does not match this year-last pattern.
    for match in re.finditer(r'(?<![\d-])(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})(?!\d)', quote):
        if 1 <= int(match[1]) <= 12 and 1 <= int(match[2]) <= 12:
            return False
    published = _datetime(published_at)
    if not re.search(r'\b(?:19|20)\d{2}\b', quote):
        if published is None or deadline - published > timedelta(days=90):
            return False
    local = deadline
    clock = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\s+([A-Za-z]{2,5})\b', quote, re.I)
    # Also notice a zone after a 24-hour clock. Unknown abbreviations in either
    # form must not turn into an assumed UTC cutoff.
    clock24 = re.search(r'\b(\d{1,2}):(\d{2})\s+([A-Z]{2,5})\b', quote)
    zone = clock[4].upper() if clock else clock24[3] if clock24 else None
    if zone in {'AM', 'PM', 'ON', 'TO', 'FOR', 'AND', 'THE', 'SHARP'}:
        zone = None
    if zone:
        if zone not in _ZONE_HOURS:
            return False
        local = deadline.astimezone(timezone(timedelta(hours=_ZONE_HOURS[zone])))
        hour = int(clock[1]) % 12 + (12 if clock[3].lower().startswith('p') else 0) if clock else int(clock24[1])
        minute = int(clock[2] or 0) if clock else int(clock24[2])
        if (local.hour, local.minute) != (hour, minute):
            return False
    return all(_WEEKDAYS[match[1].lower()] == local.weekday()
               for match in _WEEKDAY_RE.finditer(quote))


def _supported(title, description, deadline, published_at, evidence):
    if not isinstance(evidence, dict):
        return False
    spans = evidence.get('deadline')
    if not isinstance(spans, list) or len(spans) != 1 or not isinstance(spans[0], dict):
        return False
    span = spans[0]
    source_name = span.get('source')
    if not isinstance(source_name, str):
        return False
    source = {'title': title, 'description': description}.get(source_name)
    start, end = span.get('start'), span.get('end')
    if (source is None or type(start) is not int or type(end) is not int or
            not 0 <= start < end <= len(source) or span.get('quote') != source[start:end] or
            _datetime(span.get('value')) != deadline):
        return False
    quote = source[start:end]
    if (not _ENTRY.search(quote) or _UNRELATED.search(quote)
            or not _sane_deadline_quote(quote, deadline, published_at)):
        return False
    # Reparse the actual source, not the untrusted span's asserted datetime.
    canonical = build_evidence(title, description, {'deadline': deadline.isoformat()}, published_at=published_at)
    return any(all(span.get(key) == actual.get(key) for key in ('source', 'start', 'end', 'quote'))
               and _datetime(actual.get('value')) == deadline for actual in canonical['deadline'])


def assess_availability(*, title='', description='', deadline=None, source_fetched_at=None,
                        published_at=None, stored_availability='unknown', evidence=None,
                        now=None, max_check_age_hours=DEFAULT_MAX_CHECK_AGE_HOURS):
    """Return only JSONable policy metadata; original text never leaves this helper.

    SQLite-naive source times are UTC. Publication age never implies closure.
    Evidence must match original offsets and independently reparse to the deadline.
    """
    clock = _datetime(now) if now is not None else datetime.now(timezone.utc)
    if clock is None:
        raise ValueError('now must be an ISO datetime or datetime')
    if isinstance(max_check_age_hours, bool) or not isinstance(max_check_age_hours, (int, float)) or not 0 < max_check_age_hours <= 720:
        raise ValueError('max_check_age_hours must be greater than zero and at most 720')
    title, description = title or '', description or ''
    checked, cutoff = _datetime(source_fetched_at), _datetime(deadline)
    due = checked + timedelta(hours=max_check_age_hours) if checked else None
    state, reasons = 'unconfirmed', []
    if stored_availability == 'expired':
        state, reasons = 'expired', ['stored_expired']
    elif stored_availability == 'closed' or _CLOSED.search(title + '\n' + description) or CLOSED_RE.search(title + '\n' + description):
        state, reasons = 'closed', ['source_reports_closed']
    elif cutoff is None:
        reasons = ['deadline_missing' if deadline is None else 'deadline_invalid']
    elif not _supported(title, description, cutoff, published_at, evidence):
        reasons = ['deadline_evidence_unsupported']
    elif cutoff <= clock:
        state, reasons = 'expired', ['supported_deadline_passed']
    elif _OPENING_CLAIM.search(title + '\n' + description):
        # A closing date alone cannot resolve a separate opening-date claim.
        # Until start evidence has its own supported parser, abstain rather than
        # advertise a future or ambiguous entry period as currently open.
        reasons = ['entry_opening_requires_review']
    elif checked is None:
        state, reasons = 'needs_recheck', ['source_check_missing_or_invalid']
    elif checked > clock:
        reasons = ['source_check_in_future']
    elif checked < clock - timedelta(days=SOURCE_CACHE_MAX_AGE_DAYS):
        state, reasons = 'needs_recheck', ['source_cache_stale']
    elif clock > due:
        state, reasons = 'needs_recheck', ['source_check_too_old']
    else:
        state, reasons = DISCOVERABLE_STATE, ['supported_future_entry_deadline', 'recent_source_check']
    return dict(state=state, discoverable=state == DISCOVERABLE_STATE, label=_LABELS[state], reasons=reasons,
        source_checked_at=checked.isoformat() if checked else None,
        recheck_due_at=due.isoformat() if due else None, deadline=cutoff.isoformat() if cutoff else None)
