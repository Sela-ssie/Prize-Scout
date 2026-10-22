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

The title is a second input, kept deliberately separate from the description. A creator
who runs a giveaway usually says so in the title ("$500 PS5 GIVEAWAY"), and the old
description-only path rejected those outright at ``no_giveaway_indicator`` even though
the title had already been fetched at zero extra quota. The title is *not* concatenated
into the description: ``short_text``, ``hashtag_ratio`` and ``is_tag_dump`` are ratios
over the description, and prepending a title would move all three for every video.
"""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import dateparser
from dateparser.search import search_dates

EXTRACTOR_VERSION = "2.4.3"

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
    "expiry", "last day", "enter by", "entries close", "entry deadline", "ended on", "closed on",
]
HOWTO_KEYWORDS = [
    "how to enter", "to enter", "enter by", "steps to enter", "subscribe", "subscribing",
    "like", "liking", "comment", "commenting", "follow", "following", "retweet",
    "retweeting", "share", "sharing", "tag", "tagging", "participate", "participating",
]

NEGATION_PATTERNS = [
    r"\bno\s+giveaway", r"\bnot\s+a\s+giveaway", r"\bno\s+giveaways\b", r"\bno\s+prize",
    r"\bfake\s+giveaway", r"\bgiveaway\s+scam", r"\bthis\s+is\s+not\s+a\s+contest",
    r"\bnot\s+(?:running|hosting|doing|holding)\s+(?:a|any|this)?\s*(?:giveaway|contest|sweepstakes)",
    r"\bnot\s+giving\s+(?:anything|any\s+\w+)?\s*away",
    r"\bnot\s+affiliated\s+with\b",
    r"\bimpersonating\b",
]

# A giveaway that has already closed is not a live giveaway. Detecting this is worth more
# than any regex tweak: it was the single highest-confidence false positive in evaluation.
CLOSED_GIVEAWAY_PATTERNS = [
    r"\bgiveaway\s+(?:has\s+)?(?:ended|closed|finished|concluded)\b",
    r"\bgiveaway\s+is\s+(?:now\s+)?(?:over|closed|ended|finished)\b",
    r"\bentries\s+(?:are\s+)?(?:now\s+)?closed\b",
    r"\bcontest\s+(?:has\s+)?(?:ended|closed)\b",
    r"\bwinner[s]?\s+(?:has|have|was|were)\s+(?:already\s+)?(?:been\s+)?(?:announced|picked|chosen|selected|contacted|drawn)\b",
    r"\bwinner[s]?\s+has\s+already\s+been\b",
    r"\bwinner[s]?\s+announce(?:d|ment)\b(?!\s+soon)",
    r"\bwinner[s]?\s+reveal(?:ed)?\b",
    r"\bcongratulations\s+to\s+(?:the|our|my)\s+winner",
    r"\bno\s+longer\s+accepting\s+entries\b",
    r"\bthis\s+giveaway\s+is\s+over\b",
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
CLOSED_RE = re.compile("|".join(CLOSED_GIVEAWAY_PATTERNS), re.IGNORECASE)
MUSIC_RE = _boundary_re(MUSIC_VIDEO_INDICATORS)
NEAR_NEGATION_RE = _boundary_re(["no", "not", "fake", "never", "without"])
ACTION_RE = _boundary_re([
    "subscribe", "subscribing", "like", "liking", "comment", "commenting", "follow",
    "following", "retweet", "share", "sharing", "tag", "tagging", "join", "joining",
])

# An entry instruction is an imperative: the action verb opens a clause. "Comment your
# favourite game below" is one; "timestamps are in the pinned comment" is not. Without
# this, any description mentioning its own comments section looked like entry steps.
_CLAUSE_OPENERS = {"to", "and", "or", "just", "please", "simply", "is", "do", "must", "then",
                    "also", "you", "by", "through", "start"}
_CLAUSE_BOUNDARY_RE = re.compile(r"(?:^|[.!?:;,\n\-–—•]|\d[\.\)⃣]|️⃣|✔|✅|👉)\s*$")


def _is_imperative_action(segment: str, start: int) -> bool:
    head = segment[:start]
    if _CLAUSE_BOUNDARY_RE.search(head):
        return True
    words = re.findall(r"[A-Za-z']+", head)
    return bool(words) and words[-1].lower() in _CLAUSE_OPENERS


# Distinguishes offering a giveaway from talking about one. Meta videos - countdowns,
# reactions, winner interviews - use the same vocabulary as the real thing.
FIRST_PERSON_OFFER_RE = re.compile(
    r"\b(?:i|we)\s*(?:'m|'re|\s+am|\s+are)?\s+(?:giving\s+away|giveaway|hosting|running|doing|holding)\b"
    r"|\bgiving\s+away\b(?=[^.!?]*\b(?:to\s+(?:one|two|three|a|you)|winner|subscriber|viewer)\b)"
    r"|\benter\s+to\s+win\b|\bhow\s+to\s+enter\b|\bto\s+enter\b"
    r"|\bwin\s+(?:a|an|this|the|one)\b"
    r"|\bi\s+(?:will|'ll)\s+(?:pick|choose|select|announce|draw)\b"
    r"|\bwinners?\s+will\s+be\b|\bwinners?\s+drawn\b|\bentries\s+close\b",
    re.IGNORECASE,
)
META_DISCUSSION_RE = re.compile(
    r"\breacting\s+to\b|\breaction\s+to\b|\bcountdown\b|\btop\s+\d+\b"
    r"|\bwho\s+won\b|\bhave\s+been\s+asking\b|\bwhat\s+(?:really\s+)?happens\s+when\b"
    r"|\b(?:their|his|her|another|other)\s+(?:own\s+)?(?:giveaway|contest|channel)\b"
    r"|\bthis\s+creator\b|\blast\s+year\b|\bi\s+sat\s+down\s+with\b"
    r"|\bcommentary\s+and\s+analysis\b|\boriginal\s+video\b",
    re.IGNORECASE,
)
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
    "automotive": r"(?:car|auto(?:motive)?)\s+(?:detailing\s+)?(?:kits?|accessories|care\s+kits?)|floor[-\s]+mats?|dash\s*cam|tires?|tyres?|tool\s+(?:sets?|kits?)",
    "car": r"\bcar\b|corvette|mustang|tesla",
    "bike": r"\bbikes?\b|bicycles?|motorcycle|scooter",
    "drone": r"\bdrone\b",
    "archery": r"(?:[A-Za-z][\w-]*\s+[A-Z]\d[\w-]*\s+)?compound\s+bows?|(?:archery|bow)(?:\s+(?:products|\d+[- ]inch|front[- ]facing)){0,3}\s+stabilizers?",
    "camera": r"trail\s+cameras?|\bcameras?\b|\bgopro\b|\bdslr\b",
    # Match furniture compounds first so a TV stand is not reduced to electronics.
    "furniture": r"tv\s+stands?|coffee\s+tables?|chairs?|desks?",
    "TV": r"\btv\b|television|smart\s+tv",
    "headphones": r"headphones|earbuds",
    "smartwatch": r"smart\s?watch|apple\s+watch",
    "Robux": r"\brobux\b",
    "V-Bucks": r"v-?bucks",
    "diamonds": r"\bdiamonds?\b",
    "merch": r"\bmerch\b|merchandise",
    "console": r"\bconsole\b",
    "board games": r"board\s+games?",
    # Concrete goods and experiences only. Keep this an allowlist: arbitrary text after
    # "win" includes reviews, slogans and SEO rather than necessarily naming a prize.
    "books": r"(?:signed\s+)?(?:books?|novels?|cookbooks?)(?:\s+(?:bundle|set))?|box(?:ed)?\s+set\s+of\s+books",
    "beauty": r"(?:makeup|cosmetics?|skincare|skin\s+care|beauty)\s+(?:sets?|kits?|bundles?|hampers?)|perfume|fragrance|lipstick|eyeshadow\s+palette|hair\s+dryer",
    "fashion": r"handbags?|sneakers?|trainers|hoodies?|t-shirts?|jackets?|jewel(?:lery|ry)|necklaces?|bracelets?|earrings?",
    "home": r"air\s+fryer|stand\s+mixer|blender|coffee\s+(?:maker|machine)|espresso\s+machine|cookware(?:\s+set)?|vacuum(?:\s+cleaner)?|bedding\s+set|duvet|sofa|home\s+decor\s+(?:set|bundle)",
    "food": r"(?:food|snack|chocolate|coffee|tea)\s+(?:hampers?|baskets?|bundles?|boxes|box)|restaurant\s+(?:vouchers?|meals?)|meal\s+kits?|chocolates?",
    "travel": r"(?:all[- ]inclusive\s+)?(?:trips?|holidays?|vacations?)\s+(?:to|for)\s+(?:two|[A-Z][a-z]+)|weekend\s+(?:getaways?|breaks?)|hotel\s+(?:stays?|vouchers?)|cruises?|(?:concert|festival|theat(?:er|re)|cinema|theme\s+park|sporting\s+event)\s+tickets?|spa\s+(?:days?|vouchers?)",
    "outdoors": r"(?:camping\s+)?tents?|sleeping\s+bags?|hiking\s+(?:boots?|backpacks?)|fishing\s+(?:rods?|reels?|tackle)|(?:golf|tennis)\s+(?:clubs?|rackets?|racquets?|sets?)|footballs?|basketballs?|yoga\s+mats?|paddleboards?|kayaks?",
    "crafts": r"sewing\s+machines?|(?:art|painting|drawing|craft|knitting|crochet)\s+(?:supplies|sets?|kits?|bundles?)|yarn\s+(?:bundles?|sets?)|watercolou?r\s+(?:sets?|paints?)",
    "pet supplies": r"(?:dog|cat|pet)\s+(?:food|treats?|toys?|beds?|supplies|hampers?|bundles?)|aquariums?|cat\s+trees?",
}
ITEM_RE = re.compile(
    r"\b(?:" + "|".join(f"(?P<{k.replace(' ', '_').replace('-', '_')}>{v})" for k, v in PRIZE_ITEM_PATTERNS.items()) + r")\b",
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

_DATEISH_RE = re.compile(
    r"\d{1,4}[/-]\d{1,2}"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d"
    r"|\d\s*(?:st|nd|rd|th)?\s+(?:of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"|\btoday\b|\btomorrow\b|\bmidnight\b"
    r"|\bnext\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    r"|\bin\s+\d+\s+(?:days?|weeks?|hours?)\b"
    r"|\bend\s+of\s+(?:the\s+)?(?:month|week|year)\b",
    re.IGNORECASE,
)

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
    "away", "giving", "grabs", "worth",
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


# "I have a spare Switch and I want one of you to have it" announces a prize without
# using any prize vocabulary. Kept deliberately narrow: a bare "I have an iPhone" must
# not become a prize.
OFFER_CUE_RE = re.compile(
    r"\bup\s+for\s+grabs\b"
    r"|\bprizes?\s+(?:is|are|include[sd]?|will\s+be)\b"
    r"|\b(?:i|we)\s+(?:have|got|am\s+giving|are\s+giving)\b[^.!?]{0,90}"
    r"\b(?:one\s+of\s+you|to\s+you|for\s+one\s+of\s+you|to\s+have\s+it|to\s+give\s+away|away)\b",
    re.IGNORECASE,
)


# Award lists may follow instructions without repeating "win" for every item.
# A list cue alone never establishes an offer, and footer/review text ends scope.
_AWARD_LIST_RE = re.compile(
    r'^(?:[-*•]\s*|\d+[.)]\s*|there\s+will\s+be\s+(?:a|an|one|two|three|\d+)\b'
    r'|(?:the\s+)?(?:prizes?|awards?)\s*(?::|include\b|are\b)'
    r'|(?:a|an|one|two|three|\d+)\s+)', re.I,
)
_NONAWARD_LIST_RE = re.compile(r'\b(?:reviews?|reviewing|testing|comparison|compare|accessories|affiliate|discount|buy|shop|purchase)\b', re.I)
_AWARD_NOUN_RE = re.compile(r'\b(?:prizes?|giveaways?|contest|sweepstakes)\b', re.I)
_AWARD_ALLOCATION_RE = re.compile(r'\b(?:winners?\s+for|each\s+(?:getting|gets?)\s+to\s+(?:pick|choose))\b', re.I)
_AWARD_LINK_RE = re.compile(r'\b(?:will|can|could)\s+win\b|\b(?:correct|right)\s+(?:answers?|responses?)\b[^.!?]{0,70}\bwin\b', re.I)
_PRIZE_FOOTER_RE = re.compile(
    r'^(?:affiliate\s+links?|shop\b|buy\b|products?\s+(?:used|featured|reviewed)|'
    r'links?\s+to\s+(?:see|buy|shop)|accessor(?:y|ies)\s+reviews?)\b'
    r'|\b(?:affiliate\s+links?|discount\s+code|coupon\s+code)\b', re.I,
)
_AWARD_BRIDGE_RE = re.compile(
    r'\b(?:you\s+(?:need\s+to|must|have\s+to)|(?:i|we)\s+will\s+ask|'
    r'correct\s+answers?|right\s+answers?|if\s+you|to\s+enter|how\s+to\s+enter)\b', re.I,
)


def _prize_candidates(text: str) -> List[str]:
    out = []
    remaining, characters = 0, 0
    numbered = False
    for seg in _segments(text):
        if re.fullmatch(r'\d+[.)]', seg):
            numbered = bool(remaining)
            continue
        if is_tag_dump(seg) or _PRIZE_FOOTER_RE.search(seg):
            remaining = 0
            continue
        if NEAR_NEGATION_RE.search(seg) and PRIZE_RE.search(seg):
            remaining = 0
            continue
        mentions = list(ITEM_RE.finditer(seg))
        if mentions and _AWARD_ALLOCATION_RE.search(seg) and all(
            any(re.search(r'(?<!\w)' + re.escape(mention.group().removesuffix('s')) + r's?(?!\w)', prior, re.I)
                for prior in out) for mention in mentions
        ):
            continue  # Repeated winner/allocation prose is not another prize.
        direct = bool(PRIZE_RE.search(seg) or STRONG_PHRASE_RE.search(seg) or OFFER_CUE_RE.search(seg))
        listed = bool(remaining and characters + len(seg) <= 1200 and (numbered or _AWARD_LIST_RE.search(seg))
                      and ITEM_RE.search(seg) and not _NONAWARD_LIST_RE.search(seg))
        numbered = False
        if direct or listed:
            out.append(seg)
        anchor = bool(FIRST_PERSON_OFFER_RE.search(seg) or OFFER_CUE_RE.search(seg) or (_AWARD_LINK_RE.search(seg) and _AWARD_NOUN_RE.search(seg)))
        if anchor:
            remaining, characters = 6, 0
        elif remaining:
            remaining -= 1
            characters += len(seg)
            if not (listed or _AWARD_BRIDGE_RE.search(seg)):
                remaining = 0
    return out


def extract_prizes(text: str, include_money_labels: bool = True) -> List[Dict]:
    """Structured prizes: a list of {item, value_usd}, most valuable first.

    Each money amount is assigned to the single nearest item mention within a window, so
    "Win a Nintendo Switch and a $500 Amazon gift card" credits $500 to the gift card
    alone rather than to both items.

    ``include_money_labels`` controls the fallback that names an unclaimed amount from the
    words after it. That reads a prose sentence correctly ("$100 Steam Giftcard") but turns
    a title into nonsense -- "$1200 PC GIVEAWAY!" became the prize "PC GIVEAWAY" and
    "$13K Gaming Giveaway Yet" became "Gaming Giveaway Yet". Titles pass False, so a title
    only ever contributes a prize that ITEM_RE actually recognises.
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
                # Enforce the spatial window before adding the directional penalty.
                # Otherwise forward matches can stretch 145 characters while backward
                # matches get only 45, attaching remote amounts to unrelated goods.
                if max(start - mm.end(), mm.start() - end, 0) > 45:
                    continue
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
            if money_index in claimed or not include_money_labels:
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

def _parse_date(candidate: str, reference: Optional[datetime] = None) -> Optional[datetime]:
    settings = dict(_DATEPARSER_SETTINGS)
    if reference is not None:
        settings["RELATIVE_BASE"] = reference
    parsed = dateparser.parse(candidate, settings=settings)
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def find_first_date(text: str, reference: Optional[datetime] = None) -> Optional[datetime]:
    """Parse date evidence with a supplied clock, never an implicit machine clock.

    Calendar-only cutoffs last through that UTC calendar day. Timezones explicitly
    supplied with a clock time are honoured. Missing source timezone remains an
    approximation, not a verified eligibility boundary.
    """
    settings = dict(_DATEPARSER_SETTINGS)
    if reference is not None:
        settings["RELATIVE_BASE"] = reference
    # search_dates can interpret standalone years and incidental words as dates.
    # Require recognisable date evidence before invoking it.
    if not _DATEISH_RE.search(text):
        return None
    candidates = []
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = match.group(0)
            tail = text[match.end():]
            time_match = re.match(
                r"\s*(?:at\s+|[-–]\s*)?(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)"
                r"(?:\s+(?:UTC|GMT|EST|EDT|CST|CDT|MST|MDT|PST|PDT))?)", tail, re.I)
            if time_match:
                candidate += " " + time_match.group(1)
            candidates.append(candidate)
    if not candidates:
        candidates = [matched for matched, _ in (search_dates(text, settings=settings) or [])
                      if _DATEISH_RE.search(matched)]
    for candidate in candidates:
        parsed = _parse_date(candidate, reference)
        if parsed:
            explicit_time = re.search(r"\d:\d|\d\s*(?:am|pm)\b|midnight|noon|hours?", candidate, re.I)
            return parsed if explicit_time else parsed.replace(hour=23, minute=59, second=59, microsecond=0)
    return None


def find_deadline(
    text: str,
    now: Optional[datetime] = None,
    published_at: Optional[datetime] = None,
) -> Optional[datetime]:
    """Return explicit entry cutoff evidence, including expired dates.

    Publication anchors relative phrases and missing years; reprocessing a video must
    not move its deadline. Unrelated upload/draw dates and SEO 'live today' are not
    entry cutoffs. Unknown is preferable to inventing an expiry.
    """
    now = now or datetime.now(timezone.utc)
    reference = published_at or now
    for match in DEADLINE_RE.finditer(text):
        window = re.split(r"\n|[.!?]\s", text[match.end():match.end() + 160], maxsplit=1)[0]
        deadline = find_first_date(window, reference=reference)
        if deadline and deadline - reference <= MAX_DEADLINE_HORIZON:
            return deadline
    return None


# --------------------------------------------------------------------------------------
# How to enter
# --------------------------------------------------------------------------------------

_ENTRY_INSTRUCTION_RE = re.compile(
    r'\b(?:how|steps)\s+to\s+enter\b|\bto\s+enter\b'
    r'|\benter\s+(?:by|here|at|via|to\s+win|the\s+(?:giveaway|contest|sweepstakes))\b'
    r'|\bparticipat(?:e|ing)\s+by\b'
    r'|\b(?:giveaway|contest|sweepstakes|entry)\s+(?:rules|instructions|steps)\b'
    r'|\bfor\s+(?:(?:an?|one|extra)\s+)?entr(?:y|ies)\b'
    r'|\bfor\s+a\s+chance\s+to\s+win\b'
    r'|\ball\s+you\s+(?:need|have)\s+to\s+do\b'
    r'|\bentry\s+is\s+(?:completely\s+)?free\b', re.I,
)
_ENTRY_HEADING_RE = re.compile(
    r'^(?:how\s+to\s+enter|steps\s+to\s+enter|to\s+enter'
    r'|(?:giveaway|contest|sweepstakes|entry)\s+(?:rules|instructions|steps))\s*[:!\-–—]?$', re.I,
)
_ENTRY_METHOD_RE = re.compile(r'\b(?:submit|fill|complete|visit|register|sign\s+up|enter\s+(?:here|at|via))\b', re.I)
_PROMOTION_PURPOSE_RE = re.compile(r'\bfor\s+more\b|\bstay\s+tuned\b|\bsupport\s+(?:the|our|my)\s+channel\b', re.I)
_SOCIAL_FOOTER_RE = re.compile(r'\bon\s+(?:all\s+)?social\s+media\b|\bfollow\s+me\s+on\b', re.I)


_REQUIRED_CONDITION_RE = re.compile(
    r'\b(?:you|entrants?|participants?)\s+(?:will\s+)?'
    r'(?:need\s+to|must|have\s+to|are\s+required\s+to)\s+\w+', re.I,
)
_WINNING_CONDITION_RE = re.compile(
    r'\bif\s+you\b[^.!?]{0,120}\byou\s+(?:will\s+)?win\b'
    r'|\b(?:correct|right)\s+(?:answers?|responses?)\b[^.!?]{0,80}\b(?:win|wins|prize)\b'
    r'|\b(?:to|and)\s+win\b', re.I,
)
_CONDITION_BRIDGE_RE = re.compile(r'\b(?:i|we)\s+(?:will|shall)\s+ask\b', re.I)


def find_howto(text: str) -> Optional[str]:
    """Require local entry context; titles never turn channel footers into rules.

    Blank paragraphs and unrelated sentences end an instruction section. A direct
    offer may introduce adjacent imperative steps, while explicit entry wording
    can establish a new section anywhere in the source.
    """
    collected = []
    for block in re.split(r'\n\s*\n', text):
        entry_section = offer_context = False
        segments = _segments(block)
        condition_tail = -1
        for index, seg in enumerate(segments):
            if re.fullmatch(r'\d+[.)]', seg):
                continue  # Sentence splitting can separate numbered-list markers.
            if is_tag_dump(seg) or _PRIZE_FOOTER_RE.search(seg):
                entry_section = offer_context = False
                condition_tail = -1
                continue
            if _ENTRY_HEADING_RE.fullmatch(seg):
                entry_section, offer_context = True, False
                continue
            # A required action plus a nearby explicit winning consequence is
            # meaningful even without social verbs. Never search across paragraphs,
            # affiliate sections, or unrelated filler to manufacture a condition.
            window = []
            for following in segments[index:index + 3]:
                if _PRIZE_FOOTER_RE.search(following) or _PROMOTION_PURPOSE_RE.search(following):
                    break
                window.append(following)
                if _WINNING_CONDITION_RE.search(following):
                    break
                if len(window) > 1 and not _CONDITION_BRIDGE_RE.search(following):
                    break
            winning = bool(window and _WINNING_CONDITION_RE.search(window[-1]))
            required = bool(_REQUIRED_CONDITION_RE.search(seg))
            method = any(_is_imperative_action(seg, match.start()) for match in _ENTRY_METHOD_RE.finditer(seg))
            recent_offer = bool(FIRST_PERSON_OFFER_RE.search(seg) or OFFER_CUE_RE.search(seg))
            for prior in reversed(segments[max(0, index - 3):index]):
                if _PRIZE_FOOTER_RE.search(prior):
                    break
                if FIRST_PERSON_OFFER_RE.search(prior) or OFFER_CUE_RE.search(prior) or (_AWARD_LINK_RE.search(prior) and _AWARD_NOUN_RE.search(prior)):
                    recent_offer = True
                    break
                if not (_AWARD_BRIDGE_RE.search(prior) or _AWARD_LIST_RE.search(prior) or _CONDITION_BRIDGE_RE.search(prior)):
                    break
            conditional = winning and recent_offer and (required or (method and (entry_section or offer_context)))
            if conditional:
                condition_tail = index + len(window) - 1
            continuation = index <= condition_tail
            linked = bool(_ENTRY_INSTRUCTION_RE.search(seg))
            action = bool(ACTION_RE.search(seg) or _ENTRY_METHOD_RE.search(seg))
            imperative = any(_is_imperative_action(seg, m.start()) for m in ACTION_RE.finditer(seg))
            contextual = imperative and (entry_section or offer_context)
            generic = _PROMOTION_PURPOSE_RE.search(seg) or (not entry_section and _SOCIAL_FOOTER_RE.search(seg))
            if continuation or (linked and action) or (contextual and not generic):
                collected.append(_tidy(seg))
                entry_section = True
            else:
                entry_section = False
            offer_context = bool(FIRST_PERSON_OFFER_RE.search(seg) or OFFER_CUE_RE.search(seg))
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
        "strong_keyword": 1.0,
        "strong_phrase": 1.1,
        "entry_keyword": 0.5,
        "first_person_offer": 1.8,
        "meta_discussion": -2.2,
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
        "title_strong": 1.3,
        "title_prize_value": 0.8,
        "title_music": -1.5,
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
    title: str = "",
    title_prizes: Optional[List[Dict]] = None,
) -> Dict[str, float]:
    tokens = _TOKEN_RE.findall(text) or [""]
    title = title or ""
    title_prizes = title_prizes or []
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
        "first_person_offer": 1.0 if FIRST_PERSON_OFFER_RE.search(text) else 0.0,
        "meta_discussion": 1.0 if META_DISCUSSION_RE.search(text) else 0.0,
        "hashtag_ratio": min(1.0, text.count("#") / len(tokens)),
        # Title signals stay their own features rather than being folded into the text
        # ones. A giveaway word in a title is stronger evidence than the same word in a
        # description -- titles are written for humans, descriptions are half SEO.
        "title_strong": 1.0 if (STRONG_RE.search(title) or STRONG_PHRASE_RE.search(title)) else 0.0,
        "title_prize_value": 1.0 if any(p["value_usd"] is not None for p in title_prizes) else 0.0,
        "title_music": 1.0 if MUSIC_RE.search(title) else 0.0,
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


def _merge_prizes(primary: List[Dict], extra: List[Dict], limit: int = 10) -> List[Dict]:
    """Description prizes first, then any the title names that they missed."""
    merged = list(primary)
    seen = {(p["item"].lower(), p["value_usd"]) for p in merged}
    for prize in extra:
        key = (prize["item"].lower(), prize["value_usd"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(prize)
    merged.sort(key=lambda p: (p["value_usd"] is None, -(p["value_usd"] or 0)))
    return merged[:limit]


def analyse(
    text: str,
    title: str = "",
    published_at=None,
    now: Optional[datetime] = None,
    dump_fraction: Optional[float] = None,
) -> Dict:
    """Fields and features for one video, with no accept/reject gating.

    ``extract`` gates on top of this and ``scripts/calibrate.py`` fits on it. They have to
    see identical features: weights fitted on features the runtime never computes describe
    a model that does not exist.
    """
    text = text or ""
    title = (title or "").strip()
    now = now or datetime.now(timezone.utc)
    published = _coerce_dt(published_at)

    if dump_fraction is None:
        dump_fraction = tag_dump_fraction(text)

    # Titles are clickbait: "$5000 Gaming PC Build" names no prize. Only mine a title once
    # it has actually announced a giveaway.
    title_strong = bool(STRONG_RE.search(title) or STRONG_PHRASE_RE.search(title))
    title_prizes = extract_prizes(title, include_money_labels=False) if title_strong else []
    prizes = _merge_prizes(extract_prizes(text), title_prizes)

    # Deadline and how-to stay description-only. A title has no room for entry steps, and
    # find_deadline over a title reads stray years ("2026 Giveaway") as a closing date.
    deadline = find_deadline(text, now=now, published_at=published)
    howto = find_howto(text)

    return {
        "prizes": prizes,
        "title_prizes": title_prizes,
        "title_strong": title_strong,
        "deadline": deadline,
        "howto": howto,
        "dump_fraction": dump_fraction,
        "features": compute_features(text, prizes, deadline, howto,
                                     dump_fraction=dump_fraction,
                                     title=title, title_prizes=title_prizes),
    }


def extract(
    text: str,
    published_at=None,
    now: Optional[datetime] = None,
    title: Optional[str] = None,
    weights: Optional[Dict] = None,
) -> Dict:
    """Full extraction result, always returned (never None) so callers can see the score."""
    text = text or ""
    title = (title or "").strip()
    now = now or datetime.now(timezone.utc)

    result = {
        "is_giveaway": False,
        "prizes": [],
        "prize_summary": None,
        "deadline": None,
        "howto": None,
        "confidence": 0.0,
        "extractor_version": EXTRACTOR_VERSION,
        "rejected_because": None,
        "title_only": False,
        "availability": "unknown",
    }

    # A title-bearing giveaway can have a near-empty description, so the length floor is
    # over both fields rather than the description alone.
    if len(text) + len(title) < 50:
        result["rejected_because"] = "too_short"
        return result
    # These two run over the title as well. Admitting titles widens what gets in, and
    # "GIVEAWAY WINNER ANNOUNCED" is exactly what that widening would otherwise let past.
    if NEGATION_RE.search(text) or NEGATION_RE.search(title):
        result["rejected_because"] = "negation"
        return result
    if CLOSED_RE.search(text) or CLOSED_RE.search(title):
        result["rejected_because"] = "already_closed"
        result["availability"] = "expired"
        closed_deadline = find_deadline(text, now=now, published_at=_coerce_dt(published_at))
        result["deadline"] = closed_deadline.isoformat() if closed_deadline else None
        return result
    if MUSIC_RE.search(text[:300]):
        result["rejected_because"] = "music_video"
        return result

    analysis = analyse(text, title=title, published_at=published_at, now=now)
    text_strong = bool(STRONG_RE.search(text) or STRONG_PHRASE_RE.search(text))
    if not (text_strong or analysis["title_strong"]):
        # An entry keyword alone ("entry", "participate") is far too weak to admit.
        result["rejected_because"] = "no_giveaway_indicator"
        return result
    if analysis["dump_fraction"] > 0.85:
        result["rejected_because"] = "tag_dump"
        return result

    # A generic subscribe request plus a GIVEAWAY headline is not an entry offer.
    # Require a prize, cutoff, entry wording or an explicit first-person offer.
    if not (analysis["prizes"] or analysis["deadline"] or ENTRY_RE.search(text)
            or STRONG_PHRASE_RE.search(text) or FIRST_PERSON_OFFER_RE.search(text)):
        result["rejected_because"] = "unsupported_giveaway_mention"
        return result

    deadline = analysis["deadline"]
    confidence = confidence_from_features(analysis["features"], weights=weights)

    result.update(
        prizes=analysis["prizes"],
        prize_summary=summarize_prizes(analysis["prizes"]),
        deadline=deadline.isoformat() if deadline else None,
        howto=analysis["howto"],
        confidence=round(confidence, 4),
        features=analysis["features"],
        title_only=analysis["title_strong"] and not text_strong,
    )
    if deadline and deadline < now:
        result["availability"] = "expired"
        result["rejected_because"] = "expired"
        return result
    result["is_giveaway"] = confidence >= (weights if weights is not None else _WEIGHTS).get("threshold", 0.5)
    if result["is_giveaway"] and deadline:
        result["availability"] = "active"
    if not result["is_giveaway"]:
        result["rejected_because"] = "below_threshold"
    return result


def extract_giveaways_from_description(
    text: str, published_at=None, now=None, title: Optional[str] = None
) -> Optional[Dict]:
    """Backwards-compatible wrapper: returns None when this is not a giveaway."""
    res = extract(text, published_at=published_at, now=now, title=title)
    return giveaway_payload(res)


def giveaway_payload(res: Dict) -> Optional[Dict]:
    """Convert one full analysis to storage fields without running extraction twice."""
    if not res["is_giveaway"]:
        return None
    return {
        "prizes": res["prize_summary"],
        "prizes_structured": res["prizes"],
        "deadline": res["deadline"],
        "howto": res["howto"],
        "confidence": res["confidence"],
        "extractor_version": res["extractor_version"],
        "title_only": res["title_only"],
        "availability": res["availability"],
    }
