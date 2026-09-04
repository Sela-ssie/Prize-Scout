"""Giveaway extraction from YouTube video descriptions.

Design notes
------------
Every keyword test uses a precompiled word-boundary regex. The previous version used
``any(k in text for k in KEYWORDS)``, so "win" matched inside *viewing, twin, window,
rewind, drawing, swing, winter*. That is how an attorney-client disclaimer ("...just
because you're viewing this information") and "Album Winter in America (1974)" were both
stored in production as PRIZES.

Confidence is a linear model over named features whose weights live in
``extractor_weights.json`` so they can be refit from labelled data
(``scripts/calibrate.py``) rather than hand-tuned in code.
"""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import dateparser
from dateparser.search import search_dates

EXTRACTOR_VERSION = "2.0.0"

_WEIGHTS_PATH = Path(__file__).resolve().parents[1] / "extractor_weights.json"

# --------------------------------------------------------------------------------------
# Keyword lists. Add words here; never add an ad-hoc `in` substring check.
# --------------------------------------------------------------------------------------

STRONG_GIVEAWAY_KEYWORDS = [
    "giveaway", "giveaways", "contest", "sweepstakes", "sweepstake", "raffle",
]
STRONG_GIVEAWAY_PHRASES = [
    r"enter\s+to\s+win", r"chance\s+to\s+win", r"win\s+a\s+free", r"free\s+giveaway",
    r"we[' ]?re\s+giving\s+away", r"giving\s+away", r"prize\s+draw", r"lucky\s+winner",
]
PRIZE_KEYWORDS = ["prize", "prizes", "win", "winning", "winner", "winners", "giveaway"]
ENTRY_KEYWORDS = [
    "to enter", "enter by", "how to enter", "entry", "entries", "eligible",
    "eligibility", "participate",
]
DEADLINE_KEYWORDS = [
    "deadline", "ends", "ends on", "end date", "closes", "closing", "until", "expires",
    "expiry", "last day", "winner announced", "drawn on", "draw date", "before",
]
HOWTO_KEYWORDS = [
    "how to enter", "to enter", "enter by", "steps to enter", "subscribe", "like",
    "comment", "follow", "retweet", "share", "tag",
]

NEGATION_PATTERNS = [
    r"\bno\s+giveaway", r"\bnot\s+a\s+giveaway", r"\bno\s+giveaways\b", r"\bno\s+prize",
    r"\bfake\s+giveaway", r"\bgiveaway\s+scam", r"\bthis\s+is\s+not\s+a\s+contest",
]
MUSIC_VIDEO_INDICATORS = [
    "official video", "official lyric", "music video", "official audio", "lyric video",
    "official music", "audio only",
]


def _boundary_re(words) -> re.Pattern:
    """Word-boundary alternation over literal keywords. This is the whole point."""
    return re.compile(r"\b(?:%s)\b" % "|".join(re.escape(w) for w in words), re.IGNORECASE)


def _phrase_re(patterns) -> re.Pattern:
    return re.compile(r"\b(?:%s)\b" % "|".join(patterns), re.IGNORECASE)


STRONG_RE = _boundary_re(STRONG_GIVEAWAY_KEYWORDS)
STRONG_PHRASE_RE = _phrase_re(STRONG_GIVEAWAY_PHRASES)
PRIZE_RE = _boundary_re(PRIZE_KEYWORDS)
ENTRY_RE = _boundary_re(ENTRY_KEYWORDS)
DEADLINE_RE = _boundary_re(DEADLINE_KEYWORDS)
HOWTO_RE = _boundary_re(HOWTO_KEYWORDS)
NEGATION_RE = re.compile("|".join(NEGATION_PATTERNS), re.IGNORECASE)
MUSIC_RE = _boundary_re(MUSIC_VIDEO_INDICATORS)
NEAR_NEGATION_RE = _boundary_re(["no", "not", "fake", "never", "without"])
ACTION_RE = _boundary_re(["subscribe", "like", "comment", "follow", "retweet", "share", "tag", "join"])
MULTI_STEP_RE = re.compile(r"(\b(?:and|then)\b|[1-9][⃣\.\)]|[1-9]️?⃣|✔|✅|\n\s*[-*•])", re.IGNORECASE)

# --------------------------------------------------------------------------------------
# Prize items and money
# --------------------------------------------------------------------------------------

PRIZE_ITEM_PATTERNS = {
    "iPhone": r"i\s?phone(?:\s+\d{1,2}(?:\s*pro)?(?:\s*max)?)?",
    "Samsung Galaxy": r"galaxy\s+s\d{1,2}(?:\s*ultra)?|s\d{2}\s*ultra",
    "PlayStation": r"playstation(?:\s*\d)?|ps[45]\b",
    "Xbox": r"xbox(?:\s+series\s+[sx])?",
    "Nintendo Switch": r"nintendo\s+switch(?:\s*\d)?|switch\s+oled",
    "MacBook": r"macbook(?:\s+(?:air|pro))?",
    "iPad": r"ipad(?:\s+(?:air|pro|mini))?",
    "AirPods": r"airpods(?:\s+(?:pro|max))?",
    "laptop": r"laptop",
    "gaming PC": r"gaming\s+(?:pc|rig)",
    "GPU": r"rtx\s*\d{4}|geforce|radeon\s+rx\s*\d{4}",
    "gift card": r"gift\s?cards?|giftcards?",
    "cash": r"\bcash\b|\bmoney\b",
    "car": r"\bcar\b|corvette|mustang|tesla",
    "bike": r"\bbike\b|motorcycle|scooter",
    "drone": r"\bdrone\b",
    "camera": r"\bcamera\b|\bgopro\b|\bdslr\b",
    "TV": r"\btv\b|television|smart\s+tv",
    "headphones": r"headphones|earbuds",
    "smartwatch": r"smart\s?watch|apple\s+watch",
    "Robux": r"\brobux\b",
    "V-Bucks": r"v-?bucks",
    "diamonds": r"\bdiamonds?\b",
    "merch": r"\bmerch\b|merchandise",
    "console": r"\bconsole\b",
}
ITEM_RE = re.compile(
    "|".join(f"(?P<{k.replace(' ', '_').replace('-', '_')}>{v})" for k, v in PRIZE_ITEM_PATTERNS.items()),
    re.IGNORECASE,
)
_GROUP_TO_ITEM = {k.replace(" ", "_").replace("-", "_"): k for k in PRIZE_ITEM_PATTERNS}

MONEY_RE = re.compile(
    r"(?:(?P<sym>[$£€₹])\s?(?P<amt1>\d[\d,]*(?:\.\d+)?)\s*(?P<mult1>k\b|m\b|thousand\b|million\b)?)"
    r"|(?:(?P<amt2>\d[\d,]*(?:\.\d+)?)\s*(?P<mult2>k\b|m\b|thousand\b|million\b)?\s*(?P<word>dollars?|usd|eur|gbp|pounds?|euros?|rupees?|inr)\b)",
    re.IGNORECASE,
)
_MULTIPLIERS = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "million": 1_000_000}
# Only USD-denominated amounts populate value_usd; we do not invent exchange rates.
_USD_SYMBOLS = {"$"}
_USD_WORDS = {"dollar", "dollars", "usd"}

DATE_PATTERNS = [
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    re.compile(
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        r"aug(?:ust)?|sep(?:t)?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|"
        r"apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t)?(?:ember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?),?\s*\d{4}\b",
        re.IGNORECASE,
    ),
]

_DATEPARSER_SETTINGS = {
    "RETURN_AS_TIMEZONE_AWARE": True,
    "TIMEZONE": "UTC",
    "TO_TIMEZONE": "UTC",
    "PREFER_DATES_FROM": "future",
}
MAX_DEADLINE_HORIZON = timedelta(days=730)

_TOKEN_RE = re.compile(r"\S+")
_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_URL_RE = re.compile(r"https?://\S+")


# --------------------------------------------------------------------------------------
# SEO / hashtag dump rejection
# --------------------------------------------------------------------------------------

def is_tag_dump(text: str) -> bool:
    """True when text reads as a keyword/hashtag list rather than prose.

    Thresholds were set against the 49 distinct prize strings the previous extractor
    actually wrote to the database, of which 18 are genuine SEO or hashtag dumps.
    """
    if not text:
        return False
    stripped = _URL_RE.sub(" ", text)
    tokens = _TOKEN_RE.findall(stripped)
    n = len(tokens)
    if n < 4:
        return False

    hashtags = stripped.count("#")
    if hashtags >= 3 and hashtags / n > 0.15:
        return True

    commas = stripped.count(",")
    words = [w.lower() for w in _WORD_RE.findall(stripped)]
    terminators = len(re.findall(r"[.!?](?:\s|$)", stripped))

    if n >= 25 and commas / n > 0.18:
        return True

    # Comma-separated *phrases* rather than clauses: many short fragments, none of
    # which is a sentence. This catches "steal a brainrot, steal a brainrot roblox, ..."
    # even when the comma ratio alone stays under threshold.
    if commas >= 7:
        fragments = [f.strip() for f in stripped.split(",") if f.strip()]
        if len(fragments) >= 8:
            avg_words = sum(len(f.split()) for f in fragments) / len(fragments)
            if avg_words <= 7 and terminators <= max(1, len(fragments) // 8):
                return True

    # Heavy token repetition is the signature of a keyword-stuffing block.
    if len(words) >= 30 and len(set(words)) / len(words) < 0.35:
        return True

    return False


def tag_dump_fraction(text: str) -> float:
    """Share of the text (by token count) that reads as a tag dump.

    Descriptions frequently end with an SEO block after legitimate giveaway prose, so a
    whole-text reject would discard real giveaways. This is a graded signal instead.
    """
    segments = _segments(text)
    if not segments:
        return 0.0
    total = dumped = 0
    for seg in segments:
        count = len(_TOKEN_RE.findall(seg))
        total += count
        if is_tag_dump(seg):
            dumped += count
    return dumped / total if total else 0.0


# --------------------------------------------------------------------------------------
# Prizes
# --------------------------------------------------------------------------------------

def _money_to_usd(m: re.Match) -> Optional[float]:
    sym = m.group("sym")
    word = (m.group("word") or "").lower().rstrip("s")
    if sym is not None:
        if sym not in _USD_SYMBOLS:
            return None
        amount, mult = m.group("amt1"), m.group("mult1")
    else:
        if word not in _USD_WORDS and word + "s" not in _USD_WORDS:
            return None
        amount, mult = m.group("amt2"), m.group("mult2")
    try:
        value = float(amount.replace(",", ""))
    except (TypeError, ValueError):
        return None
    if mult:
        value *= _MULTIPLIERS.get(mult.lower().strip(), 1)
    return value


def _item_name(m: re.Match) -> str:
    for group, name in _GROUP_TO_ITEM.items():
        if m.group(group):
            return name
    return m.group(0)


_BACKWARD_PENALTY = 100.0
# Cues meaning the amount refers to something already named: "a PS5 worth $499".
_VALUE_CUE_RE = re.compile(r"\b(?:worth|valued\s+at|value(?:d)?(?:\s+of)?|priced\s+at|rrp|msrp)\b\s*[:\-]?\s*$", re.IGNORECASE)

_BRAND_STOPWORDS = {
    "win", "a", "an", "the", "and", "or", "to", "for", "new", "free", "get", "of", "one",
    "your", "our", "my", "this", "that", "is", "are", "we", "i", "you", "enter", "giveaway",
}


def _brand_prefix(segment: str, start: int) -> str:
    """Keep a leading proper noun: 'Amazon gift card' rather than 'gift card'."""
    head = segment[max(0, start - 30): start]
    words = re.findall(r"[A-Za-z][\w'-]*", head)
    if words and words[-1][:1].isupper() and words[-1].lower() not in _BRAND_STOPWORDS:
        return words[-1] + " "
    return ""


def _segments(text: str) -> List[str]:
    parts: List[str] = []
    for line in text.splitlines():
        parts.extend(p.strip() for p in re.split(r"(?<=[.!?])\s+", line) if p.strip())
    return parts


def _prize_candidates(text: str) -> List[str]:
    out = []
    for seg in _segments(text):
        if is_tag_dump(seg):
            continue
        if NEAR_NEGATION_RE.search(seg) and PRIZE_RE.search(seg):
            continue
        if PRIZE_RE.search(seg) or STRONG_PHRASE_RE.search(seg):
            out.append(seg)
    return out


def extract_prizes(text: str) -> List[Dict]:
    """Structured prizes: a list of {item, value_usd}, most valuable first.

    Each money amount is assigned to the single nearest item mention within a window, so
    "Win a Nintendo Switch and a $500 Amazon gift card" credits $500 to the gift card
    alone rather than to both items.
    """
    prizes: List[Dict] = []
    seen = set()

    for seg in _prize_candidates(text):
        items = [(m.start(), m.end(), _tidy(_brand_prefix(seg, m.start()) + m.group(0)))
                 for m in ITEM_RE.finditer(seg)]
        monies = []
        for mm in MONEY_RE.finditer(seg):
            value = _money_to_usd(mm)
            if value is not None:
                monies.append((mm, value))

        # Score every (money, item) pair, then assign greedily from the best pair down,
        # so each amount and each item is used at most once.
        pairs = []
        for money_index, (mm, value) in enumerate(monies):
            backward_cue = bool(_VALUE_CUE_RE.search(seg[max(0, mm.start() - 16): mm.start()]))
            for item_index, (start, end, _) in enumerate(items):
                if start <= mm.start() <= end:
                    distance = 0.0
                elif start >= mm.end():
                    distance = float(start - mm.end())
                    if backward_cue:
                        distance += _BACKWARD_PENALTY  # "worth $499" refers backwards
                elif backward_cue:
                    distance = float(mm.start() - end)
                else:
                    # "$500 gift card": in English a price modifies the noun that
                    # follows it, so forward matches win over closer backward ones.
                    distance = float(mm.start() - end) + _BACKWARD_PENALTY
                if distance <= 45 + _BACKWARD_PENALTY:
                    pairs.append((distance, money_index, item_index))

        values: Dict[int, float] = {}
        claimed = set()
        taken_items = set()
        for distance, money_index, item_index in sorted(pairs):
            if money_index in claimed or item_index in taken_items:
                continue
            values[item_index] = monies[money_index][1]
            claimed.add(money_index)
            taken_items.add(item_index)

        for index, (_, _, name) in enumerate(items):
            value = values.get(index)
            key = (name.lower(), value)
            if key in seen:
                continue
            seen.add(key)
            prizes.append({"item": name, "value_usd": value})

        for money_index, (mm, value) in enumerate(monies):
            if money_index in claimed:
                continue
            label = _money_label(seg, mm)
            key = (label.lower(), value)
            if key in seen:
                continue
            seen.add(key)
            prizes.append({"item": label, "value_usd": value})

    prizes.sort(key=lambda p: (p["value_usd"] is None, -(p["value_usd"] or 0)))
    return prizes[:10]


def _money_label(segment: str, m: re.Match) -> str:
    """Name the thing an amount buys: '$100 Steam Giftcard' -> 'Steam Giftcard'."""
    tail = segment[m.end(): m.end() + 40]
    words = re.findall(r"[A-Za-z][\w'-]*", tail)[:3]
    keep = []
    for w in words:
        if w.lower() in {"and", "or", "to", "for", "the", "a", "in", "of", "plus", "worth"}:
            break
        keep.append(w)
    return " ".join(keep) if keep else "cash"


def _tidy(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def summarize_prizes(prizes: List[Dict]) -> Optional[str]:
    """Human-readable string kept alongside the structured list, for display."""
    if not prizes:
        return None
    parts = []
    for p in prizes[:5]:
        if p["value_usd"] is not None:
            amount = f"${p['value_usd']:,.0f}" if p["value_usd"] == int(p["value_usd"]) else f"${p['value_usd']:,.2f}"
            parts.append(f"{p['item']} ({amount})" if p["item"].lower() != "cash" else f"{amount} cash")
        else:
            parts.append(p["item"])
    return ", ".join(parts)


# --------------------------------------------------------------------------------------
# Deadlines
# --------------------------------------------------------------------------------------

def _parse_date(candidate: str) -> Optional[datetime]:
    parsed = dateparser.parse(candidate, settings=_DATEPARSER_SETTINGS)
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def find_first_date(text: str) -> Optional[datetime]:
    for pattern in DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            parsed = _parse_date(m.group(0))
            if parsed:
                return parsed
    found = search_dates(text, settings=_DATEPARSER_SETTINGS)
    if found:
        dt = found[0][1]
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _plausible_deadline(dt: Optional[datetime], now: datetime, published_at: Optional[datetime]) -> bool:
    if dt is None:
        return False
    if dt < now:
        return False  # a giveaway that already closed is not a deadline worth showing
    if dt - now > MAX_DEADLINE_HORIZON:
        return False
    if published_at is not None and dt.date() == published_at.date():
        # Matching the upload date almost always means we matched the video's own
        # publication date rather than a closing date.
        return False
    return True


def find_deadline(
    text: str,
    now: Optional[datetime] = None,
    published_at: Optional[datetime] = None,
) -> Optional[datetime]:
    now = now or datetime.now(timezone.utc)
    lower = text.lower()

    for m in DEADLINE_RE.finditer(lower):
        window = text[m.start(): m.start() + 200]
        dt = find_first_date(window)
        if _plausible_deadline(dt, now, published_at):
            return dt

    dt = find_first_date(text)
    return dt if _plausible_deadline(dt, now, published_at) else None


# --------------------------------------------------------------------------------------
# How to enter
# --------------------------------------------------------------------------------------

def find_howto(text: str) -> Optional[str]:
    collected = []
    for seg in _segments(text):
        if is_tag_dump(seg):
            continue
        if HOWTO_RE.search(seg) and (ACTION_RE.search(seg) or ENTRY_RE.search(seg)):
            collected.append(_tidy(seg))
    if not collected:
        return None
    return " ".join(collected[:3])[:500]


# --------------------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    "bias": -2.6,
    "threshold": 0.5,
    "calibrated": False,
    "weights": {
        "strong_keyword": 1.6,
        "strong_phrase": 1.5,
        "entry_keyword": 0.9,
        "prize_with_value": 1.4,
        "prize_item": 0.9,
        "has_deadline": 1.1,
        "has_howto": 0.9,
        "howto_multi_step": 0.5,
        "is_tag_dump": -1.8,
        "music_video": -2.0,
        "short_text": -0.9,
        "no_fields": -1.5,
        "hashtag_ratio": -1.2,
    },
}


def load_weights() -> Dict:
    if _WEIGHTS_PATH.exists():
        try:
            data = json.loads(_WEIGHTS_PATH.read_text())
            if "weights" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return DEFAULT_WEIGHTS


_WEIGHTS = load_weights()


def reload_weights() -> Dict:
    """Re-read extractor_weights.json (used by scripts/calibrate.py and tests)."""
    global _WEIGHTS
    _WEIGHTS = load_weights()
    return _WEIGHTS


def compute_features(
    text: str,
    prizes: List[Dict],
    deadline: Optional[datetime],
    howto: Optional[str],
    dump_fraction: Optional[float] = None,
) -> Dict[str, float]:
    tokens = _TOKEN_RE.findall(text) or [""]
    return {
        "strong_keyword": 1.0 if STRONG_RE.search(text) else 0.0,
        "strong_phrase": 1.0 if STRONG_PHRASE_RE.search(text) else 0.0,
        "entry_keyword": 1.0 if ENTRY_RE.search(text) else 0.0,
        "prize_with_value": 1.0 if any(p["value_usd"] is not None for p in prizes) else 0.0,
        "prize_item": 1.0 if prizes else 0.0,
        "has_deadline": 1.0 if deadline else 0.0,
        "has_howto": 1.0 if howto else 0.0,
        "howto_multi_step": 1.0 if howto and MULTI_STEP_RE.search(howto) else 0.0,
        "is_tag_dump": tag_dump_fraction(text) if dump_fraction is None else dump_fraction,
        "music_video": 1.0 if MUSIC_RE.search(text[:300]) else 0.0,
        "short_text": 1.0 if len(text) < 120 else 0.0,
        "no_fields": 1.0 if not (prizes or deadline or howto) else 0.0,
        "hashtag_ratio": min(1.0, text.count("#") / len(tokens)),
    }


def confidence_from_features(features: Dict[str, float], weights: Optional[Dict] = None) -> float:
    w = weights or _WEIGHTS
    z = w.get("bias", 0.0) + sum(w["weights"].get(k, 0.0) * v for k, v in features.items())
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------

def _coerce_dt(value) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if isinstance(value, datetime) and value.tzinfo is None else value
    parsed = _parse_date(str(value))
    return parsed


def extract(
    text: str,
    published_at=None,
    now: Optional[datetime] = None,
) -> Dict:
    """Full extraction result, always returned (never None) so callers can see the score."""
    text = text or ""
    now = now or datetime.now(timezone.utc)
    published = _coerce_dt(published_at)

    result = {
        "is_giveaway": False,
        "prizes": [],
        "prize_summary": None,
        "deadline": None,
        "howto": None,
        "confidence": 0.0,
        "extractor_version": EXTRACTOR_VERSION,
        "rejected_because": None,
    }

    if len(text) < 50:
        result["rejected_because"] = "too_short"
        return result
    if NEGATION_RE.search(text):
        result["rejected_because"] = "negation"
        return result
    if MUSIC_RE.search(text[:300]):
        result["rejected_because"] = "music_video"
        return result
    if not (STRONG_RE.search(text) or STRONG_PHRASE_RE.search(text)):
        # An entry keyword alone ("entry", "participate") is far too weak to admit.
        result["rejected_because"] = "no_giveaway_indicator"
        return result
    dump_fraction = tag_dump_fraction(text)
    if dump_fraction > 0.85:
        result["rejected_because"] = "tag_dump"
        return result

    prizes = extract_prizes(text)
    deadline = find_deadline(text, now=now, published_at=published)
    howto = find_howto(text)

    features = compute_features(text, prizes, deadline, howto, dump_fraction=dump_fraction)
    confidence = confidence_from_features(features)

    result.update(
        prizes=prizes,
        prize_summary=summarize_prizes(prizes),
        deadline=deadline.isoformat() if deadline else None,
        howto=howto,
        confidence=round(confidence, 4),
        features=features,
    )
    result["is_giveaway"] = confidence >= _WEIGHTS.get("threshold", 0.5)
    if not result["is_giveaway"]:
        result["rejected_because"] = "below_threshold"
    return result


def extract_giveaways_from_description(text: str, published_at=None, now=None) -> Optional[Dict]:
    """Backwards-compatible wrapper: returns None when this is not a giveaway."""
    res = extract(text, published_at=published_at, now=now)
    if not res["is_giveaway"]:
        return None
    return {
        "prizes": res["prize_summary"],
        "prizes_structured": res["prizes"],
        "deadline": res["deadline"],
        "howto": res["howto"],
        "confidence": res["confidence"],
        "extractor_version": res["extractor_version"],
    }
