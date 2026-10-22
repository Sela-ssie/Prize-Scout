"""Browse categories derived from identified prizes, never affiliate/sidebar text."""
import re
from typing import Literal

Category = Literal['all', 'tech', 'gaming', 'books', 'beauty', 'fashion', 'home',
                   'food', 'travel', 'sports', 'arts', 'pets', 'automotive', 'cash', 'other']

CATEGORIES = {
    'arts': 'Arts & crafts',
    'beauty': 'Beauty & wellness',
    'books': 'Books & reading',
    'automotive': 'Cars & transport',
    'cash': 'Cash & gift cards',
    'fashion': 'Fashion & accessories',
    'food': 'Food & drink',
    'gaming': 'Gaming',
    'home': 'Home & kitchen',
    'pets': 'Pets',
    'sports': 'Sports & outdoors',
    'tech': 'Tech & electronics',
    'travel': 'Travel & experiences',
    'other': 'Other prizes',
}

_PATTERNS = {
    'tech': r'iphone|galaxy|macbook|ipad|airpods|laptops?|gaming\s+(?:pc|rig)|gpu|rtx|geforce|radeon|drones?|cameras?|gopro|dslr|tv(?!\s+stands?\b)|television|headphones|earbuds|smart\s?watch|apple\s+watch|phones?|tablets?',
    'gaming': r'playstation|ps[45]|xbox|nintendo|switch|consoles?|robux|v-?bucks|gaming\s+(?:pc|rig)|(?:video|board)\s+games?|steam\s+gift\s?cards?',
    'books': r'books?|cookbooks?|novels?|book\s+sets?|box\s+sets?|kindle|e-?readers?',
    'beauty': r'makeup|make-up|cosmetics?|skincare|skin\s+care|beauty|lipsticks?|perfume|fragrances?|hair\s+care|spa|wellness',
    'fashion': r'clothing|clothes|hoodies?|t-?shirts?|sneakers?|shoes?|handbags?|jewel(?:le)?ry|necklaces?|bracelets?|watches|wristwatch|merch(?:andise)?|apparel|dresses|dress|earrings?',
    'home': r'cookware|kitchen|blenders?|mixers?|air\s+fryers?|coffee\s+(?:makers?|machines?|tables?)|tv\s+stands?|chairs?|desks?|espresso\s+machines?|vacuum|furniture|sofas?|mattresses?|bedding|duvet|home\s+decor|candles?|appliances?',
    'food': r'food|snacks?|coffee(?!\s+(?:tables?|makers?|machines?)\b)|tea|chocolate|groceries|grocery|meal|dinner|restaurant|baking|cooki(?:e|es)|hampers?',
    'travel': r'trips?|vacations?|holidays?|flights?|hotel|travel|cruises?|getaways?|tickets?|concert|festival|experiences?|spa',
    'sports': r'compound\s+bows?|archery|bow\s+stabilizers?|trail\s+cameras?|bicycles?|bikes?|sports?|fitness|camping|tents?|sleeping\s+bags?|hiking|fishing|golf|tennis|footballs?|basketballs?|yoga|kayaks?|paddleboards?|skateboards?|sneakers?|running\s+shoes|gym',
    'arts': r'(?:art|painting|drawing|knitting|crochet)\s+(?:supplies|sets?|kits?|bundles?)|craft\s+kits?|crafts?|paint\s+sets?|watercolou?rs?|sketchbooks?|sewing|yarn|guitars?|musical\s+instruments?',
    'pets': r'pets?|dogs?|cats?|aquarium',
    'automotive': r'cars?|auto(?:motive)?|corvette|mustang|tesla|motorcycles?|scooters?|trucks?|vehicles?|floor[-\s]+mats?|dash\s*cam|tires?|tyres?|tool\s+(?:sets?|kits?)',
    'cash': r'cash|money|gift\s?cards?|giftcards?|vouchers?',
}
_COMPILED = {key: re.compile(r'\b(?:' + pattern + r')\b', re.IGNORECASE)
             for key, pattern in _PATTERNS.items()}


def classify_prizes(prizes, summary=None):
    """Allow multiple categories; unknown products remain discoverable under Other."""
    names = [p.get('item', '') for p in prizes if isinstance(p, dict)
             and isinstance(p.get('item'), str)] if isinstance(prizes, list) else []
    text = ' '.join(names) or summary or ''
    matches = [key for key, pattern in _COMPILED.items() if pattern.search(text)]
    return matches or ['other']
