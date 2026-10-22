# Availability and promotion policy

PrizeScout reports what indexed source material says. It does not verify that an
entry is accepted, that a creator will award a prize, or that a promotion will
work at checkout. A displayed status is a discovery aid, not a trust or
redemption guarantee.

## Giveaway discovery

The web app's search and browse use `current_only=true` by default. API clients
must explicitly request this mode; the compatibility default includes uncertain
mentions. The current-only mode returns only mentions
that meet the current-evidence rule. Users may opt into **Include unconfirmed
mentions** to review indexed mentions whose current status is not established.
Legacy availability filters remain available in either mode; they do not change
the evidence standard or independently verify a result.

The only discoverable state is `source_reported_open`. It means the source
supports an entry period that appears open at the time of its latest check. To
receive this state, a result must have:

- one exact, reproducible deadline span in the original description;
- deadline text that refers to an entry or giveaway, not an unrelated date;
- a parsed deadline strictly later than the current time; and
- a source fetch/check time that is not in the future and is no more than 48
  hours old.

The evidence span must match the original text at its recorded offsets and
reparse to the same deadline. A stored date or extracted deadline by itself is
not enough. Multiple or unsupported deadline claims remain unconfirmed rather
than selecting one silently.

Calendar qualifiers must agree with that date: ambiguous numeric day/month order,
a conflicting weekday, a missing year projected more than 90 days past publication,
or an unsupported/dropped timezone prevents current discovery. These checks abstain
instead of silently accepting the parser's interpretation. Missing-year December
to January transitions within that window can still qualify.

A separate dated or ambiguous opening-period claim also withholds a result:
a future closing date does not prove entries have started. Opening-date parsing
is not implemented; these claims require review even if they appear to describe
a past start. Explicit present wording such as “entries open now” does not by
itself create that ambiguity.

The label must say **“Source reports an open entry period; not independently
verified”** or equivalent wording. Never call a result “verified active.” Show
the source-check time separately from the video's publication date. An old
publication date alone never means a giveaway has expired: only an explicit
closed claim or a supported deadline that has passed can produce a closed or
expired assessment.

Other assessment states are:

- `expired`: the supported entry deadline has passed, or the indexed record is
  already marked expired;
- `closed`: the source says the entry period is closed;
- `unconfirmed`: required current evidence is missing, ambiguous, or invalid, or
  the recorded source check time is in the future;
- `needs_recheck`: a source check is missing or invalid, the retained source is
  stale, or its check time is outside the allowed 48-hour window.

These states explain the evidence available to the index. They do not establish
whether a creator honors a promotion or whether an entrant qualifies. The
48-hour source-check limit is deliberately stricter than the cache-retention
limit: a record can remain in the index without being eligible for default
discovery.

If another retained, nonfuture source with the same recognized entry link explicitly
reports closure or supports a passed entry deadline, the campaign is withheld
from current discovery. This applies even if that source is a negative giveaway
extraction. Source refresh or removal can clear that conflict; the system does
not infer reopening from a majority vote. This conservative policy can withhold
a reused entry link. Links that cannot be confidently grouped remain separate.

## Source freshness and retention

The 48-hour window controls whether a source can support default current-only
discovery. Cached YouTube source data has a separate maximum 30-day retention
cycle: stale data must be refreshed or removed by that limit. A 30-day-retained
record is not thereby current. Its current-evidence assessment may already be
`needs_recheck` after 48 hours.

Offline replay and re-extraction can update derived fields, evidence spans, or
model outputs from retained text. They must not change the source fetch/check
time. Reprocessing old text is not a fresh source check and cannot renew the
48-hour discovery window. Only a successful new source fetch can do that.

The quota-bounded ingestion worker prioritizes supported, still-future offers
for rechecking after 36 hours. Outages and quota exhaustion cannot extend their
48-hour eligibility. Older inputs still share the normal 27-day refresh and
30-day purge cycle. Promo leads do not receive a redemption check through this
source-refresh process.

## Promotions are separate leads

Promotion candidates belong in a separate `/api/promotions` unverified inbox,
not in giveaway result cards. Each candidate must carry exact source evidence
for the code or affiliate disclosure and its nearby claim. Any dates are source
claims and do not prove that an offer is currently valid.

Promotion extraction is offline: it does not request arbitrary URLs, follow
redirects, or perform DNS lookups. A candidate URL is only a syntactically
screened link from source text. The inbox must not trigger navigation on behalf
of the user, add items to a cart, or attempt checkout. An affiliate or tracking
link is not evidence of a discount. Code acceptance, product eligibility,
shipping, regional rules, and final cart price remain unverified.

Store-specific and cart-specific verification are future work. Until such a
feature exists, promotion labels must stay unverified and should direct users
to review the merchant's terms themselves.

The next verification layer should distinguish an official merchant advertising
a code from a code applying to a specific cart. An authorized provider must record
the merchant, code, products, country/currency, customer eligibility, check time,
result and evidence expiry. Failures or missing context return uncertainty, not
an active label. HTTP success, a working affiliate redirect, and an indexed creator
claim cannot substitute for this check. Shopify's official
[CartDiscountCode documentation](https://shopify.dev/docs/api/storefront/latest/objects/CartDiscountCode)
illustrates the distinction: applicability is tied to the cart's current contents
and can change when those contents change. No merchant provider is implemented
in this release.

## Limits and policy tradeoffs

The evidence-first threshold favors fewer unsupported “open” results over broad
recall. It can omit legitimate giveaways whose descriptions lack an exact
deadline, whose deadline wording is ambiguous, or whose watched-channel source
has not been checked recently. A 48-hour window adds source traffic and can
reduce coverage during outages; widening it would retain more results but make
the default view less current. The 30-day retention limit is a separate data
handling requirement, not evidence of availability.

Coverage is limited to watched channels and indexed source material. Counts of
mentions do not measure extraction precision, source honesty, eligibility, or
legitimacy. Absence from the index does not mean no promotion exists.
