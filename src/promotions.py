"""Offline promotion leads, never checkout or redemption verification.

Only source-description claims are interpreted. No URLs are fetched and no DNS
resolution occurs; a syntactically public hostname is not a network safety verdict.
"""
import ipaddress
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import dateparser

_CODE_RE = re.compile(
    r'\b(?:(?P<strong>discount|coupon|promo(?:tional)?)\s+code|'
    r'(?:use|apply|enter)\s+(?:the\s+)?code|code)\b\s*(?:is\s+|[:=]\s*)?'
    r'["\'“‘]?(?P<code>[A-Za-z0-9][A-Za-z0-9_-]{0,39})(?![\w-])', re.I,
)
_BENEFIT_RE = re.compile(
    r'\b\d+(?:\.\d+)?\s*%\s*(?:off|discount|saving[s]?)\b'
    r'|[$£€]\s*\d+(?:\.\d+)?\s*(?:off|discount)\b'
    r'|\b(?:free\s+(?:shipping|delivery)|save\s+(?:\d+(?:\.\d+)?\s*%|[$£€]\s*\d+(?:\.\d+)?))(?!\w)', re.I,
)
_NONPROMO_RE = re.compile(r'\b(?:entry|game|gaming|verification|security|source|programming|python|javascript|access)\s+code\b', re.I)
_URL_RE = re.compile(r'''(?<![\w:])https?://[^\s<>"']+''', re.I)
_AFFILIATE_RE = re.compile(r'\baffiliate\s+(?:links?|disclosure|partners?)\b|\b(?:earn|receive)\b[^.!?]{0,60}\bcommission\b', re.I)
_AFFILIATE_HEADING_RE = re.compile(r'^[ \t]*(?:affiliate links?|affiliate disclosure)[ \t]*[:\-]?[ \t]*\r?\n[ \t]*$', re.I | re.M)
_NOT_AFFILIATE_RE = re.compile(r'\b(?:not\s+(?:an?\s+)?|no\s+|non[- ]?)affiliate\b', re.I)
_TRACKING_HOSTS = {'amzn.to', 'shareasale.com', 'shareasale-analytics.com', 'awin1.com',
                   'anrdoezrs.net', 'kqzyfj.com', 'tkqlhce.com', 'jdoqocy.com', 'dpbolvw.net',
                   'linksynergy.com', 'rstyle.me', 'go.magik.ly', 'prf.hn'}
_MONTH = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
_DATE = (r'(?:\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2}))?'
         r'|' + _MONTH + r'\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?'
         r'|\d{1,2}\s+' + _MONTH + r'(?:\s+\d{4})?|today|tomorrow)')
_DATE_CUE_RE = re.compile(r'\b(?P<cue>expires?(?:\s+on)?|ends?(?:\s+on)?|valid\s+until|until|starts?(?:\s+on)?|valid\s+from|begins?(?:\s+on)?)\s*[:=-]?\s*(?P<date>' + _DATE + r')(?!\w)', re.I)
_CODE_STOP = {'here', 'below', 'above', 'example', 'python', 'javascript', 'to', 'for', 'is', 'and', 'not', 'none', 'unavailable'}


def _clock(value):
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('promotion clocks must be timezone-aware')
    return parsed.astimezone(timezone.utc)


def _safe_url(value):
    value = value.rstrip('.,;!?)')
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').rstrip('.').lower()
        if parsed.scheme.lower() not in ('https', 'http') or parsed.username is not None or parsed.password is not None:
            return None
        if not host or '\\' in value or '%' in host or any(host == name or host.endswith('.' + name) for name in ('localhost', 'local', 'internal', 'invalid', 'test')):
            return None
        parsed.port  # Reject malformed ports, even though no request is performed.
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Also reject alternative numeric-IP notations used by URL parsers.
            if '.' not in host or re.fullmatch(r'(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*', host, re.I):
                return None
            host = host.encode('idna').decode('ascii')
            if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label) for label in host.split('.')):
                return None
        else:
            if not address.is_global:
                return None
        return value, host, parsed
    except (ValueError, UnicodeError):
        return None


def _known_affiliate(host, parsed):
    known = any(host == domain or host.endswith('.' + domain) for domain in _TRACKING_HOSTS)
    amazon = re.fullmatch(r'(?:[a-z0-9-]+\.)*amazon\.(?:com|co\.uk|ca|de|fr|it|es|co\.jp|com\.au|in)', host)
    query = parse_qs(parsed.query)
    return known or bool(amazon and query.get('tag')) or any(query.get(key) for key in ('affiliate_id', 'aff_id', 'affid'))


def _dates(context, published, now):
    starts, expires = [], []
    for match in _DATE_CUE_RE.finditer(context):
        raw = match['date']
        # Relative and missing-year dates belong to publication, never replay time.
        if not re.search(r'\b\d{4}\b', raw) and published is None:
            continue
        settings = {'RETURN_AS_TIMEZONE_AWARE': True, 'TIMEZONE': 'UTC', 'TO_TIMEZONE': 'UTC',
                    'RELATIVE_BASE': published or now}
        parsed = dateparser.parse(raw, languages=['en'], settings=settings)
        if parsed is not None:
            if not re.search(r'T\d{2}:\d{2}', raw) and not re.match(r'start|valid\s+from|begin', match['cue'], re.I):
                parsed = parsed.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1, microseconds=-1)
            (starts if re.match(r'start|valid\s+from|begin', match['cue'], re.I) else expires).append(parsed.isoformat())
    # Multiple cutoff claims are ambiguous; do not choose one silently.
    return (starts[0] if len(set(starts)) == 1 else None,
            expires[0] if len(set(expires)) == 1 else None)


def _clauses(description):
    for line in re.finditer(r'[^\r\n]+', description):
        offset = line.start()
        for boundary in re.finditer(r';|(?<=[.!?])\s+', line.group()):
            end = line.start() + boundary.start()
            if description[offset:end].strip():
                yield offset, end
            offset = line.start() + boundary.end()
        if description[offset:line.end()].strip():
            yield offset, line.end()


def _candidate(kind, description, start, end, published, now, *, code=None, url=None, host=None, reason, associated_claims=True):
    while start < end and description[start].isspace():
        start += 1
    while end > start and description[end - 1].isspace():
        end -= 1
    quote = description[start:end]
    benefit = _BENEFIT_RE.search(quote) if associated_claims else None
    starts, expires = _dates(quote, published, now) if associated_claims else (None, None)
    expired = expires is not None and _clock(expires) <= now
    return dict(kind=kind, code=code, url=url, merchant_host=host,
                benefit_claim=benefit.group() if benefit else None, starts_at=starts, expires_at=expires,
                availability='expired' if expired else 'unverified', last_checked_at=None,
                verification_reason=('Source claims an expiry at or before the review clock; redemption was not checked.'
                                     if expired else reason),
                evidence=dict(source='description', start=start, end=end, quote=quote))


def extract_promotions(description, *, title='', published_at=None, now=None):
    """Return JSON-compatible promo_code/affiliate_link candidates in source order.

    Evidence is an exact description clause (bounded around long-line code cues).
    Titles do not lend unrelated footer text promotion context. Dates describe
    local source claims; no candidate is ever marked verified or active.
    """
    if not description:
        return []
    now = _clock(now) if now is not None else datetime.now(timezone.utc)
    try:
        published = _clock(published_at)
    except (ValueError, TypeError, AttributeError):
        published = None
    output = []
    for start, end in _clauses(description):
        clause = description[start:end]
        matches = list(_CODE_RE.finditer(clause))
        for index, match in enumerate(matches):
            code = match['code']
            local_start = start if index == 0 else start + match.start()
            local_end = start + matches[index + 1].start() if index + 1 < len(matches) else end
            local_start = max(local_start, start + match.start() - 120)
            local_end = min(local_end, start + match.end() + 300)
            context = description[local_start:local_end]
            if code.lower() in _CODE_STOP or _NONPROMO_RE.search(context):
                continue
            if not match['strong'] and not _BENEFIT_RE.search(context):
                continue
            urls = [safe for raw in _URL_RE.findall(context) if (safe := _safe_url(raw))]
            unique = {safe[0]: safe for safe in urls}
            linked = next(iter(unique.values())) if len(unique) == 1 else None
            output.append(_candidate('promo_code', description, local_start, local_end, published, now,
                          code=code, url=linked[0] if linked else None, host=linked[1] if linked else None,
                          reason='Explicit source code/benefit claim only; merchant acceptance and redemption are unverified.'))
        url_matches = list(_URL_RE.finditer(clause))
        for match in url_matches:
            safe = _safe_url(match.group())
            if safe is None:
                continue
            url, host, parsed = safe
            # Keep disclosure local, even on an enormous unbroken description line.
            local_start, local_end = max(start, start + match.start() - 160), min(end, start + match.end() + 160)
            prefix_start = max(0, start - 120)
            heading = _AFFILIATE_HEADING_RE.search(description[prefix_start:start])
            if heading:
                local_start = prefix_start + heading.start()
            context = description[local_start:local_end]
            if _NOT_AFFILIATE_RE.search(context):
                continue
            if not (_AFFILIATE_RE.search(context) or _known_affiliate(host, parsed)):
                continue
            output.append(_candidate('affiliate_link', description, local_start, local_end, published, now,
                          url=url, host=None if any(host == domain or host.endswith('.' + domain) for domain in _TRACKING_HOSTS) else host,
                          reason='Affiliate disclosure or tracking-link signal only; no discount, commission, or redemption verified.',
                          # Shared link clauses cannot safely assign a benefit/cutoff
                          # to one merchant. Keep only the affiliate-link lead.
                          associated_claims=len(url_matches) == 1))
    return output
