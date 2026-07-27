# Amazon private-label pipeline — mapped against what exists

Working document. Answers one question: if we point this system at *find demand on
Amazon → source from Alibaba → rebrand → sell on Amazon FBA*, how much is new code?

Short answer: **the control plane transfers almost entirely, the commerce logic
transfers partially, and the TikTok-specific modules mostly don't.** The genuinely
new code is four API clients and one piece of reasoning that is the actual edge.

---

## The fork that has to be decided first

Operators are split across Canada and the UK. That is not a detail — it sets the
SP-API region, the fee schedule, the currency, the tax regime, and which trademark
office feeds Brand Registry. Three viable configurations:

| | Marketplace | Seller of record | Tax | Trademark |
|---|---|---|---|---|
| **A** | amazon.com (NA unified) | Canadian resident | US sales tax handled by Amazon; import via CBSA or direct-to-US | USPTO |
| **B** | amazon.co.uk | UK resident | EORI, import VAT, UK VAT registration | UKIPO or EUIPO |
| **C** | amazon.ca | Canadian resident | Business Number + import/export account, GST/HST, CBSA | CIPO or USPTO |

A North America unified account lets a Canadian resident sell on amazon.com, and
the US market is roughly an order of magnitude larger than amazon.ca. For a first
product, **A** is the default unless there is a reason to prefer the UK side — in
which case **B** is a genuinely separate operation with its own entity, VAT
registration, and inventory, not a setting.

Everything below is written marketplace-agnostic. The scanner takes a marketplace
as a parameter; only the fee tables and the tax/trademark path are region-specific.

---

## The pipeline, stage by stage

Each stage is marked for whether it can run unattended. `GATE` means a human
decision the system must refuse to make on its own.

### 1. Demand discovery — automatable

Scan Amazon catalogue for products with real, stable demand. Signals that matter:
BSR level and its *stability over months*, review count vs. rating (a 3.9 with
4,000 reviews is an opportunity; a 4.8 with 40 is noise), price band, number of
sellers on the listing, seasonality, and whether the top listings are brand-registered.

- **Exists:** `app/discovery/` has the right *shape* — a `TrendSource` protocol, a
  policy-driven `rank_candidates`, and a hard comment in the source that sources
  must be official or explicitly licensed.
- **Doesn't transfer:** `TrendCandidate` is engagement-shaped (`views`, `likes`,
  `comments`, `shares`, `engagement_score`). Amazon demand is rank-and-price shaped.
  This needs a sibling model, not a field rename.
- **New:** a Keepa client (BSR + price history is what actual demand estimation
  needs; Amazon's own PA-API does not expose it usefully), and an
  `AmazonOpportunity` model with its own ranking policy.

### 2. Opportunity screen — automatable, and this is where most candidates die

Run every candidate through landed cost and kill anything that cannot clear a real
margin. This is the cheapest possible test of the whole thesis.

- **Exists:** `PricingModel` / `target_price` in `app/domain/products.py` already
  models commission, transaction fees, VAT, refund reserve, and shipping, and
  already refuses to return a number when fees and margin leave no room.
- **New:** more dimensions on the same model — Amazon referral fee (category
  dependent), FBA fulfilment fee (size/weight tier), monthly storage, long-term
  storage, returns processing, and a **landed cost** input: FOB price + freight +
  duty + import VAT + prep-centre fees per unit.
- **Note:** the existing model is a good skeleton but its defaults are placeholders
  and say so. Amazon's fee schedule is public and versioned; it should be data, not
  constants.

### 3. Improvement spec from review mining — automatable, **this is the edge**

Pull the 1–3 star reviews of the incumbent listings and derive (a) the specific
failure modes buyers complain about and (b) a positioning angle that answers them.
"The hinge fails at six months, ours is steel" is the entire product strategy.

Everyone else runs the same Jungle Scout filters. Nobody is doing this well at
scale by hand. This is the part worth building carefully.

- **Exists:** nothing directly, but `app/ai/` is the right home — it already has a
  provider protocol, a hardened client with retries and a spend cap, structured
  output, and an enforced policy gate on generated text.
- **New:** review ingestion and a spec-extraction prompt with grounded output.

### 4. Supplier sourcing — partially automatable, ends in a `GATE`

Shortlist Alibaba suppliers, compare unit-price tiers, request samples, negotiate.

- **Exists:** `app/suppliers/base.py` has the protocol and validation boundary,
  and `DryRunSupplier` for testing.
- **Doesn't transfer:** `SupplierOffer` is dropship-shaped — `dispatch_hours`,
  per-order `create_fulfillment_order`. Private label is bulk-purchase shaped:
  MOQ, unit price *tiers* by quantity, lead time in weeks, sample cost,
  customisation and packaging options, certification availability.
- **`GATE` — supplier commitment.** Wiring money to a factory and judging a
  physical sample cannot be automated and must not be. This is a review item.

### 5. IP and compliance screen — partially automatable, ends in a `GATE`

A lot of what is on Alibaba infringes something. Automating product *selection*
raises the odds of listing infringing goods, and that is an account-death event
rather than a warning.

- **Exists:** `ProductPolicyGate` already blocks restricted health and performance
  claims and is enforced *inside* the AI provider so unusable copy cannot escape.
  The same pattern extends to IP.
- **New:** trademark search against whichever office matches the chosen
  marketplace (USPTO, UKIPO/EUIPO, or CIPO), a brand-name-collision check,
  gated-category detection, and a restricted/hazmat screen.
- **`GATE` — the human sign-off.** A search that finds nothing is not proof of
  non-infringement.

### 6. Brand creation — automatable up to the legal step

Name, positioning, palette, logo, packaging direction, listing copy, A+ content.

- **Exists:** `app/ai/product_copy.py` — grounded generation with a policy gate,
  and `app/media/` has `MediaAsset` with an explicit `AssetLicense` and a
  `product_match_confirmed` flag, which is exactly the discipline Amazon's image
  policy requires (the main image must be the real product, not a render).
- **Doesn't transfer:** `AdDraft` is 9:16 short-form video. Amazon listing imagery
  is a different spec entirely.
- **Higgsfield** is the right generator for secondary images, A+ modules, brand
  store assets, packaging concepts and off-Amazon ad creative — and it is already
  connected. **It cannot produce the main image.** Amazon requires the primary
  image to be an actual photograph of the actual product on pure white; a render
  or a generated composite is a listing-suppression risk and, arguably, a
  misrepresentation. That is precisely what `MediaAsset.product_match_confirmed`
  already exists to track: generated assets stay `product_match_confirmed=False`
  and are barred from the primary slot until a real photo of a received sample
  replaces them.
- **`GATE` — trademark registration.** Brand Registry requires a registered or
  pending trademark. Months and real money. No API removes it, and without it
  there is no A+ content, no brand store, and no protection from hijackers —
  which is most of the reason to private-label at all.

### 7. Listing creation — automatable behind approval

Create the listing, images, keywords, backend search terms, price.

- **Exists:** the entire approval spine. `ReviewQueue` → `ApprovedActionExecutor`
  with an allowlist → once-only execution with a durable claim → audit trail. A
  listing publish is exactly the kind of consequential action this was built for.
  `PublishGate` and its `PUBLISH_ENABLED` switch already model "approved but not
  yet permitted."
- **New:** an SP-API client (Listings Items, Catalog Items, Feeds), which becomes a
  sibling to `TikTokClient` behind the same executor allowlist.

### 8. Inbound to FBA — partially automatable, ends in a `GATE`

Factory → freight forwarder → customs → prep centre → FBA.

- **New:** FBA inbound shipment plans via SP-API; everything upstream of the
  fulfilment centre is coordination with humans and paperwork. You are the
  Importer of Record, and what that costs depends on the fork above — a Business
  Number and import/export account plus GST/HST for Canada, an EORI number and
  import VAT for the UK. Either way, duty and import tax are paid before a single
  unit sells.
- **`GATE` — purchase order and freight booking.** Capital leaves here.

### 9. Orders, inventory, repricing — automatable

- **Exists:** almost all of it. The order lifecycle state machine, the
  out-of-order event classifier, quarantine for genuine conflicts, the SLA
  watchdog, `CostLedger` and the anomaly check for unit economics, `JobRunner`
  with retries and a dead-letter queue.
- **New:** Amazon's order statuses replace TikTok's in the transition table, and
  Amazon notifications replace the TikTok webhook. The *machinery* — signature
  verification, classification, fast-forward, quarantine, real status codes —
  transfers unchanged.

---

## What that means in code

Rough line counts of the current `app/` (3,675 lines total):

| Group | Lines | Transfers |
|---|---:|---|
| Control plane — review, api, db, ops, observability, workers, audit | 1,670 | ~90%, mostly config-level changes |
| Order lifecycle — state machine, event classification, store | 333 | ~80%, status values change |
| Commerce logic — products/pricing, analytics, ai | 690 | ~50%, extended not replaced |
| TikTok/social-specific — discovery, suppliers, media, growth, retention, publish, support | 646 | ~20%, mostly protocol shapes |

So roughly **2,300 of 3,675 lines carry over**, and the new work concentrates in
four API clients (Keepa, SP-API, Alibaba, trademark search) plus the review-mining
reasoning.

The thing being reused is not really the code. It is the *stance*: every
consequential action becomes a review item, execution is once-only and audited,
adapters are disabled by default, and going live requires an attested checklist.
That stance is worth more here than it was for TikTok, because the mistakes are
more expensive — a bad listing can lose an account, and an account holds inventory.

---

## Order of work

1. **Opportunity scanner, standalone.** ✅ Economics and screening built
   (`app/amazon/`): `LandedCost` with duty and Section 301, `FeeSchedule` as
   versioned data, `unit_economics` / `break_even_price`, and a `ScreenPolicy`
   that reports every rejection reason. Remaining: the Keepa client to feed it
   real ASINs. Run it over a few thousand candidates; if nothing clears a real
   margin, nothing downstream matters and we learned it for the price of an API
   key rather than a container of stock.
2. **Review mining** on whatever survives, because the improvement spec determines
   whether there is a business, not just a listing.
3. Only then: SP-API, brand generation, and the listing path behind the existing
   approval gates.

## Unverified

Written against knowledge with a mid-2026 cutoff. Before committing to a design,
confirm: SP-API developer registration and the current Listings Items
requirements; Brand Registry's trademark criteria per office; Keepa's current API
terms and pricing; Amazon's dropshipping policy wording; whether a North America
unified account still covers a Canadian resident selling on amazon.com; and the
import/tax specifics for whichever fork is chosen.

**Already known to be a problem:** sourcing from Temu and having it shipped to the
customer is against Amazon's dropshipping policy, which prohibits any other
retailer's packing slip reaching your buyer. Temu is usable as a price signal.
It is not usable as a supply chain.
