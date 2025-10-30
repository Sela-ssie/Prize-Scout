import re
from datetime import datetime
from typing import Dict, Optional
try:
    import dateparser
    from dateparser import search as dateparser_search
    DATEPARSER_AVAILABLE = True
except Exception:
    DATEPARSER_AVAILABLE = False

DATE_PATTERNS = [
    # Simple ISO-like
    r"(\d{4}-\d{2}-\d{2})",
    # dd/mm/yyyy or dd-mm-yyyy
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    # Month name day, year
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4}",
]

# Stronger giveaway indicators
STRONG_GIVEAWAY_KEYWORDS = ["giveaway", "contest", "sweepstakes", "enter to win", "free giveaway"]
PRIZE_KEYWORDS = ["prize", "prizes", "win", "winner", "winners", "giveaway"]
ENTRY_KEYWORDS = ["to enter", "enter by", "how to enter", "entry", "entries"]
DEADLINE_KEYWORDS = ["deadline", "ends", "end date", "closes", "closing", "until", "expires", "last day"]
HOWTO_KEYWORDS = ["how to enter", "to enter", "enter by", "steps to enter", "subscribe", "like", "comment", "follow", "retweet", "share"]

# Things that indicate it's NOT a giveaway
NEGATION_PATTERNS = [r"no\s+giveaway", r"not a giveaway", r"no giveaways", r"no prize", r"fake giveaway"]
MUSIC_VIDEO_INDICATORS = ["official video", "official lyric", "music video", "official audio", "lyric video"]


def find_first_date(text: str) -> Optional[str]:
    # Try to find explicit date patterns first
    for p in DATE_PATTERNS:
        m = re.search(p, text, flags=re.IGNORECASE)
        if m:
            cand = m.group(0)
            if DATEPARSER_AVAILABLE:
                parsed = dateparser.parse(cand)
                if parsed:
                    return parsed.isoformat()
            return cand

    # if dateparser is available, try a broader search
    if DATEPARSER_AVAILABLE:
        parsed = dateparser_search.search_dates(text)
        if parsed:
            return parsed[0][1].isoformat()

    # fallback: no date found
    return None


def find_prizes(text: str) -> Optional[str]:
    """Find and extract prize information with better validation."""
    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    matches = []
    
    for s in sentences:
        sl = s.lower()
        
        # Skip if it contains negations
        if re.search(r"\b(no|not|fake)\b", sl) and any(k in sl for k in ["giveaway", "prize", "prizes"]):
            continue
            
        # Look for sentences with prize keywords
        if any(k in sl for k in PRIZE_KEYWORDS):
            matches.append(s.strip())

    if not matches:
        return None

    # Score matches: prefer ones with specific prize details
    def score(s: str) -> int:
        sc = 0
        sl = s.lower()
        
        # High priority: actual items or amounts
        if re.search(r"\$\d+|\d+\s*(dollars|USD|EUR|GBP)", s, re.IGNORECASE):
            sc += 100  # Money amounts
        if re.search(r"(iphone|playstation|xbox|switch|macbook|laptop|gift card|amazon)", sl):
            sc += 80  # Specific items
        if re.search(r"\b(win|winning|winner)\b", sl):
            sc += 50  # Win-related words
        if re.search(r"(x\d+|\d+x)", s):  # Quantities like "3x iPhone"
            sc += 30
        if len(s) > 100:  # Prefer detailed descriptions
            sc -= 20
        if len(s) < 20:  # Too short, probably not detailed
            sc -= 10
            
        return sc

    # Filter out low-quality matches and sort by score
    scored_matches = [(s, score(s)) for s in matches]
    scored_matches = [s for s, sc in scored_matches if sc > 20]  # Minimum quality threshold
    
    if not scored_matches:
        return None
        
    scored_matches.sort(key=lambda x: score(x), reverse=True)
    best = scored_matches[0]
    
    # Clean up and return
    return re.sub(r"\s+", " ", best).strip()


def find_deadline(text: str) -> Optional[str]:
    # look for keywords and dates nearby
    for k in DEADLINE_KEYWORDS:
        idx = text.lower().find(k)
        if idx != -1:
            window = text[idx : idx + 200]
            date = find_first_date(window)
            if date:
                return f"{k}: {date}"
            # fallback: return short window
            return window.strip()

    # last resort: find any date in text
    date = find_first_date(text)
    if date:
        return date
    return None


def find_howto(text: str) -> Optional[str]:
    """Find entry instructions with better detection."""
    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    collected = []
    
    for s in sentences:
        sl = s.lower()
        # Look for entry-related keywords
        if any(k in sl for k in HOWTO_KEYWORDS):
            collected.append(s.strip())
        # Also look for common entry patterns
        elif re.search(r"\b(subscribe|like|comment|follow|retweet|share|tag)\b", sl):
            if re.search(r"\b(and|to|for|entry|enter)\b", sl):  # Must have context
                collected.append(s.strip())
    
    if collected:
        # Limit to first 3 sentences to avoid too much text
        return " ".join(collected[:3])
    return None


def calculate_confidence_score(text: str, prizes: Optional[str], deadline: Optional[str], howto: Optional[str]) -> int:
    """Calculate confidence score that this is a real giveaway."""
    score = 0
    lower = text.lower()
    
    # Strong indicators
    if any(k in lower for k in STRONG_GIVEAWAY_KEYWORDS):
        score += 30
    
    # Has specific prize information
    if prizes:
        if re.search(r"\$\d+|\d+\s*(dollars|USD)", prizes, re.IGNORECASE):
            score += 25
        if re.search(r"(iphone|playstation|xbox|switch|macbook|gift card)", prizes.lower()):
            score += 25
        if len(prizes) > 20:  # Detailed prize description
            score += 15
    
    # Has deadline
    if deadline:
        score += 20
        # Has specific date format
        if re.search(r"\d{4}-\d{2}-\d{2}", str(deadline)):
            score += 10
    
    # Has entry instructions
    if howto:
        score += 20
        # Multiple entry steps
        if re.search(r"\band\b", howto.lower()):
            score += 10
    
    # Check for entry-related words
    if any(k in lower for k in ENTRY_KEYWORDS):
        score += 15
    
    # Penalties
    # Check if it's likely a music video or other non-giveaway content
    if any(indicator in lower for indicator in MUSIC_VIDEO_INDICATORS):
        score -= 40
    
    # Very short description (likely not detailed enough)
    if len(text) < 100:
        score -= 15
    
    # Just has keywords but no substance
    if not prizes and not deadline and not howto:
        score -= 30
    
    return score


def extract_giveaways_from_description(text: str) -> Optional[Dict]:
    """Return structured giveaway info if likely present, otherwise None."""
    if not text or len(text) < 50:  # Increased minimum length
        return None

    lower = text.lower()
    
    # Quick rejection: avoid obvious negatives
    for p in NEGATION_PATTERNS:
        if re.search(p, lower):
            return None
    
    # Quick rejection: music videos and other non-giveaway content
    if any(indicator in lower for indicator in MUSIC_VIDEO_INDICATORS):
        title_section = text[:200].lower()  # Check title area
        if any(indicator in title_section for indicator in MUSIC_VIDEO_INDICATORS):
            return None
    
    # Must have at least one strong giveaway indicator
    has_strong_indicator = any(k in lower for k in STRONG_GIVEAWAY_KEYWORDS)
    has_entry_keyword = any(k in lower for k in ENTRY_KEYWORDS)
    
    if not (has_strong_indicator or has_entry_keyword):
        return None

    # Extract information
    prizes = find_prizes(text)
    deadline = find_deadline(text)
    howto = find_howto(text)
    
    # Calculate confidence score
    confidence = calculate_confidence_score(text, prizes, deadline, howto)
    
    # Require minimum confidence score (lowered to get more results)
    MIN_CONFIDENCE = 30
    if confidence < MIN_CONFIDENCE:
        return None
    
    # Must have at least 1 out of 3 key pieces of information (relaxed from 2)
    info_count = sum([prizes is not None, deadline is not None, howto is not None])
    if info_count < 1:
        return None

    return {
        "prizes": prizes,
        "deadline": deadline,
        "howto": howto,
        "confidence": confidence  # Include confidence score for debugging
    }
