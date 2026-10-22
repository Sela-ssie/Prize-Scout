"""Conservative, deterministic campaign identity from supported public entry links.

Titles and prize similarity alone do not identify a campaign. Ambiguous links leave
videos separate. No redirects are followed and no external requests are made.
"""
import hashlib
import re
from urllib.parse import urlsplit

_URL = re.compile(r'https?://[^\s<>"\[\]]+', re.IGNORECASE)


def normalize_entry_url(value):
    try:
        parsed = urlsplit(value.rstrip('.,;!?)'))
        host = (parsed.hostname or '').lower().removeprefix('www.')
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.port:
            return None
    except ValueError:
        return None
    parts = parsed.path.strip('/').split('/')
    # Provider paths identify campaigns; query parameters are referral/tracking
    # values and must not split the same entry page into separate campaigns.
    if host == 'gleam.io' and re.fullmatch(r'[A-Za-z0-9]{5}', parts[0]):
        path = parts[0]
    elif host == 'woobox.com' and re.fullmatch(r'[A-Za-z0-9]{6}', parts[0]):
        path = parts[0]
    elif host == 'rafflecopter.com' and len(parts) >= 4 and parts[:2] == ['rafl', 'display']:
        if not all(re.fullmatch(r'[A-Za-z0-9_-]+', part) for part in parts[2:4]):
            return None
        path = '/'.join(parts[:4])
    elif host in ('sweepwidget.com', 'kingsumo.com') and len(parts) == 2:
        if parts[0] != ('c' if host == 'sweepwidget.com' else 'g') or not re.fullmatch(r'[A-Za-z0-9_-]{5,}', parts[1]):
            return None
        path = '/'.join(parts)
    else:
        return None
    return f'https://{host}/{path}'


def campaign_identity(description):
    links = {normalized for url in _URL.findall(description or '')
             if (normalized := normalize_entry_url(url))}
    if len(links) != 1:
        return None, None
    url = links.pop()
    key = 'entry-v1-' + hashlib.sha256(url.encode()).hexdigest()[:24]
    return key, url


def reported_differences(sources):
    """Differences are disclosed, never resolved by assuming the majority is right."""
    different = []
    for field in ('prizes', 'deadline', 'howto'):
        values = {re.sub(r'\s+', ' ', str(source[field])).strip().casefold()
                  for source in sources if source.get(field)}
        if len(values) > 1:
            different.append(field)
    return different
