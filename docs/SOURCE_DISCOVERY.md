# Additional source discovery

Research date: **2026-10-15** (client date). This is a bounded discovery pass using official brand/creator pages. It excludes the eight sources in [the starter list](../config/STARTER_CHANNELS.md). No YouTube Data API requests were made, and no watchlist or credentials were changed.

The four candidates below span aquarium/pet supplies, music, beauty, and yarn/crafts. **No currently open, video-specific giveaway was verified for these candidates.** Standing rules establish historical or recurring mechanisms; they do not establish that a particular recent upload contains an offer. A search crawl date is not a promotion date. Priority reflects evidence and category coverage, not an estimate of giveaway yield.

| Priority | Candidate | Evidence status | Identity verification | Next discovery step |
|---|---|---|---|---|
| 1 | Aquarium Co-Op | Standing rules describe repeated video-linked care packages; current offer timing unknown | Official website links to its YouTube channel | Inspect recent official uploads for an explicit care-package announcement |
| 2 | Andertons Music Co. | Standing subscriber-milestone pedal draws; current drawing timing unknown | Official giveaway page links to verified `@andertons` | Check whether current videos describe a discrete offer or only the standing program |
| 3 | Embassy of Beauty | Standing rules explicitly cover video giveaways; no recent dated instance verified | Brand's rules name its YouTube channel; exact channel URL/handle unresolved | Resolve channel from brand-owned links before spending ingestion quota |
| 4 | Hobbii | Historical monthly YouTube mechanism; recent 2026 giveaways verified on other platforms | Official brand evidence names YouTube; exact current channel URL/handle unresolved | Reconfirm monthly program and resolve current channel; do not assume regional channels are interchangeable |

## 1. Aquarium Co-Op — aquarium/pet supplies

The brand's [Care Package Giveaway Rules](https://www.aquariumcoop.com/pages/care-package-giveaway-rules) specify five winners per video, packages selected by the brand, and an entry window from video release through eight days later. The stated entry mechanism uses subscription, a like, and a comment; only one entry per person per video counts. This is concrete evidence of a repeatable YouTube promotion mechanism, although the undated rules do not prove every upload participates.

The same official page links to [the Aquarium Co-Op YouTube channel](https://www.youtube.com/aquariumcoop). This legacy channel URL is verified through the brand; an `@` handle was not independently resolved.

The rules allow entrants aged 13+, including winners outside the US, subject to shipping restrictions and country prohibitions. Employees and several connected parties are excluded. These are the care-package rules, not a universal eligibility policy for every brand promotion.

**Status:** recurring mechanism documented; no particular open draw confirmed. **Reason to prioritize:** direct video-level rules and a pet-related source absent from the starter list. Check the current video's terms before calculating any deadline.

## 2. Andertons Music Co. — musical instruments and accessories

The official [giveaway hub](https://www.andertons.co.uk/giveaways) describes a YouTube subscriber program with guitar-pedal draws at 10,000-subscriber milestones. It links directly to [Andertons Music Co., `@andertons`](https://www.youtube.com/@andertons). This verifies both identity and a recurring YouTube mechanism, without establishing when the next milestone draw happens.

The hub also describes monthly social-post prizes. Those are a separate mechanism and should not be presented as YouTube-video offers. Its email guitar offer gives July 19/27 dates without a year in the viewed text; that cannot establish an active October 2026 promotion.

The hub points to [official terms](https://www.andertons.co.uk/terms-conditions). Specific geographic and age eligibility for the standing subscriber draw was not established in this pass; worldwide retail shipping is not evidence of worldwide giveaway eligibility.

**Status:** recurring milestone program; timing unknown. **Reason to prioritize:** a verified channel and music-related prizes broaden coverage. **Limitation:** a standing subscription program may generate fewer distinct, deadline-bearing video mentions than per-upload giveaways; do not convert a subscriber threshold into a guessed date.

## 3. Embassy of Beauty — beauty and skincare

The brand's [official terms, “YouTube Giveaways” section](https://embassyofbeauty.co.uk/pages/terms-and-conditions) describe contests beginning at publication and ending two weeks later. Entrants answer the question in the particular giveaway video using its comments; prizes are specified by that video. This supports a repeatable video-linked mechanism, but no dated recent video was verified here.

Eligibility is stated as UK legal residents aged 18+ who subscribe to the brand's YouTube channel, with employee/connected-party exclusions and one entry per person. The rules describe winner announcements in the video comments and a contact process.

**Status:** standing rules; current timing unknown. Exact channel URL and handle remain **unverified**; similarly named salons should not be substituted. **Reason to retain:** clear video-specific entry and duration rules, plus UK beauty coverage. Treat it as an identity-resolution candidate before ingestion. The same page contains purchase-linked gifts and unrelated dated promotions; those do not establish a currently open YouTube giveaway.

## 4. Hobbii — yarn and crafts

Hobbii's official [German giveaway overview](https://hobbii.de/blog/hobbii-giveaways), dated **2023-03-06** in the search-indexed brand page, describes a monthly YouTube giveaway: subscribe, comment on the latest giveaway video, and a winner notification on the 15th. Prizes vary. Direct retrieval failed during this pass, so this is **historical, search-indexed primary-source evidence**, not a currently revalidated schedule. Geographic and age restrictions for that YouTube mechanism were not established; no current channel handle is asserted.

Recent brand activity is independently documented in the [2026 Frost and Bloom FAQ](https://hobbii.com/blogs/news/frost-and-bloom-cal-faq): five $100 gift cards, a March 29, 2026 end-of-day CET photo-post deadline, and March 30 contact date. Entry is through Instagram/Facebook, **not YouTube**, and that dated campaign is expired as of this research date.

**Status:** historical monthly YouTube mechanism; current continuation unknown. **Reason to retain at lower priority:** relevant craft prizes and recent brand-run giveaways, offset by unresolved channel identity and lack of a newly verified YouTube promotion. Do not treat German-page terms or an English campaign's currency as proof of global eligibility.

## Leads not advanced

- [King Arthur Baking's sweepstakes page](https://www.kingarthurbaking.com/sweepstakes) explicitly says its most recent sweepstakes has concluded. No direct video-entry evidence was established, so this pass does not recommend it as an ingestion source.
- [Missouri Star's official giveaway archive](https://blog.missouriquiltco.com/category/giveawayfabric-freebie/) contains a 500K-subscriber YouTube-comment sweepstakes. This establishes historical video-entry evidence, but a recent or continuing YouTube giveaway was not established; it was not advanced over the stronger candidates.

These are discovery notes, not eligible-offer listings or endorsements. Channel resolution and a current official video/rules check remain necessary before adding provisional identities or labeling an offer available.

## Focused channel-identity recheck — 2026-10-15

- **Andertons verified:** the “main YouTube channel” / “Subscribe on our YOUTUBE” link on the [official giveaway page](https://www.andertons.co.uk/giveaways) resolves to [Andertons Music Co., `@andertons`](https://www.youtube.com/@andertons). This is the verified handle to resolve for ingestion; a UC channel ID was not established in this pass.
- **Aquarium Co-Op remains unresolved to a canonical ID/handle:** the “Our Youtube Page” link on the [official rules page](https://www.aquariumcoop.com/pages/care-package-giveaway-rules) points to [youtube.com/aquariumcoop](https://www.youtube.com/aquariumcoop), whose retrieved page is titled “Aquarium Co-Op - YouTube.” The web response exposes a consent shell rather than a UC ID or canonical handle, and a direct metadata request could not connect from the restricted shell. The verified legacy URL is not proof of an `@aquariumcoop` handle. Do not guess a handle or copy an ID from an unrelated directory. This focused path stopped without API requests or watchlist changes.
