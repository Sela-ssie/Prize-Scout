"""Conservative, reproducible links from persisted fields to original text.

Offsets are Python string offsets into unmodified source strings. Absence of a
span means support could not be established; these are not verification claims.
"""

import re
from datetime import datetime, timezone

from src.extractor import DATE_PATTERNS, DEADLINE_RE, _DATEISH_RE, find_deadline


def _segments(text):
    # Preserve offsets while matching the extractor's sentence/line boundaries.
    for match in re.finditer(r"[^\r\n]+", text):
        line = match.group()
        offset = 0
        for boundary in re.finditer(r"(?<=[.!?])\s+", line):
            yield from _trimmed(text, match.start() + offset, match.start() + boundary.start())
            offset = boundary.end()
        yield from _trimmed(text, match.start() + offset, match.end())


def _trimmed(text, start, end):
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        yield start, end, text[start:end]


def _datetime(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def _span(source, start, end, text, value):
    return dict(source=source, start=start, end=end, quote=text[start:end], value=value)


def build_evidence(title, description, extracted, published_at=None):
    """Return only supported prize names, entry segments, and cutoff segments.

    Normalized/generated prize labels need not occur in the source and get no
    evidence. Prize spans support item names only, never their monetary values.
    Relative/missing-year cutoffs require the original publication timestamp.
    """
    result = {"prizes": [], "deadline": [], "howto": []}
    title, description = title or "", description or ""
    extracted = extracted or {}
    seen_items = set()
    for prize in extracted.get("prizes_structured") or []:
        item = prize.get("item") if isinstance(prize, dict) else None
        if not isinstance(item, str) or not item.strip() or item.casefold() in seen_items:
            continue
        seen_items.add(item.casefold())
        pattern = re.compile(r"(?<!\w)" + re.escape(item) + r"(?!\w)", re.I)
        for source, text in (("description", description), ("title", title)):
            match = pattern.search(text)
            if match:
                result["prizes"].append(_span(source, match.start(), match.end(), text, item))
                break

    howto = extracted.get("howto")
    if isinstance(howto, str) and howto.strip():
        normalized = re.sub(r"\s+", " ", howto).strip()
        consumed = 0
        for start, end, segment in _segments(description):
            candidate = re.sub(r"\s+", " ", segment)
            # Match whole source segments in output order, never invent a span
            # across omitted lines or bridge a truncated final sentence.
            match = re.search(r"(?<!\w)" + re.escape(candidate) + r"(?!\w)", normalized[consumed:])
            if match:
                result["howto"].append(_span("description", start, end, description, candidate))
                consumed += match.end()

    target = _datetime(extracted.get("deadline"))
    published = _datetime(published_at)
    if target is None:
        return result
    for start, end, segment in _segments(description):
        # Bound parser work and exclude common unrelated cutoffs. Multiple cutoff
        # cues are ambiguous, even if one happens to equal the extracted value.
        if len(segment) > 240 or len(list(DEADLINE_RE.finditer(segment))) != 1:
            continue
        if re.search(r"\b(?:sponsor\w*|coupon|discount|promo|sale|upload\w*|publish\w*|draw|announc\w*)\b", segment, re.I):
            continue
        dates = {(m.start(), m.end()) for pattern in DATE_PATTERNS for m in pattern.finditer(segment)}
        if len(list(_DATEISH_RE.finditer(segment))) != 1:
            continue
        if len(dates) > 1 or (published is None and len(dates) != 1):
            continue
        reference = published or datetime(target.year, 1, 1, tzinfo=timezone.utc)
        parsed = find_deadline(segment, now=reference, published_at=published)
        if parsed == target:
            result["deadline"].append(_span("description", start, end, description, extracted["deadline"]))
            break
    return result
