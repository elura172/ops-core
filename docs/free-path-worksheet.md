# Free-path worksheet — validating the screen without an API

Goal for this pass: **twenty candidates, and a clear read on which constraint is
doing the killing.** Not finding a winner. If a winner turns up, good, but the
useful output is calibration — whether the policy is too strict, the categories
are wrong, or the market really is that picked over.

Everything here uses Keepa's free browser extension. No subscription, no API key.

---

## Where to look

Skip Best Sellers. The top of a category is where entrenched incumbents live, and
they will all fail on `entrenched_incumbent` or `no_quality_gap`. Better hunting
grounds:

- **Amazon Movers & Shakers** in a category — rising rank, often before brands
  consolidate.
- Page 2–3 of a broad category search, not page 1.
- Products with a **3.7–4.3 rating and four figures of reviews**. That combination
  is the whole thesis: proven demand, visible dissatisfaction.

Avoid to start with: electronics with chipsets, anything with a battery (shipping
and compliance), anything requiring FDA/CPSC certification, and anything you'd
describe as a "brand" rather than a "product".

## Pre-filter before you type anything

Twenty seconds per product, on the page itself. Reject and move on if:

| Check | Reject if |
|---|---|
| Price | under $15 or over $70 |
| Reviews | under 100, or over 5,000 |
| Rating | above 4.4 |
| Sellers | more than 8 offers on the listing |
| "Brand" in title with a storefront/A+ content | brand-registered incumbent |

Anything that survives that is worth reading Keepa for.

## What to read off Keepa

The extension draws a chart under the product. You want the **Sales Rank** line —
it may need enabling in the chart's settings toggles. Set the range to the widest
the free tier gives you (usually a year).

Read off **six roughly evenly spaced rank values across the range**, oldest first.
Precision does not matter; you are measuring stability, not modelling. What matters
is catching the shape:

- A flat-ish line → steady demand. This is what you want.
- One tall spike in Q4 and flat elsewhere → seasonal trap, it will fail
  `unstable_demand`, and correctly so.
- A line trending steadily worse → dying product.

Also note the **offer count** (how many sellers) if Keepa shows it — more reliable
than eyeballing the buy-box.

## FOB estimate

Search the product on Alibaba, take the mid-point of the price range at a
realistic MOQ (say 500 units). This is an estimate and the screen treats it as
one. It only needs to be right enough to tell a 3x product from a 1.4x product.

Leave `tariff_rate` at `0.25` for anything from China unless you know the HTS code
says otherwise. Getting this wrong in the optimistic direction is the single most
common way this model kills people.

## Running it

```bash
python scripts/screen_candidate.py --template > candidates.json   # blank to fill
python scripts/screen_candidate.py --json candidates.json         # screen them all
python scripts/screen_candidate.py                                # or one, interactively
```

## Reading the result

The per-product pass/fail matters less than the **"what is doing the killing"**
block at the bottom. Roughly:

| Dominant code | What it probably means |
|---|---|
| `no_quality_gap` | Browsing categories that are already well served — move category |
| `margin_too_thin` / `roi_too_low` | Price band or FOB estimates are off, or the category is genuinely too cheap |
| `crowded_listing` | Looking at reseller categories rather than private-label ones |
| `entrenched_incumbent` | Hunting too near the top of the category |
| `unstable_demand` | Picking up seasonal or fad products |
| A flat spread across all codes | The policy is working; keep going, twenty is a small sample |

Bring the output back and the thresholds get tuned against what you actually
found, rather than the defaults, which were set from general knowledge of the
model and not from your market.

---

## One thing not to do

Do not automate the collection. Amazon's terms prohibit automated data gathering,
and this project's own stated defaults say no scraping bypasses — that principle
is in the README and it is not decoration. Keepa exists precisely because it is
the licensed way to get this data, and its paid API is the correct route to
scale once the policy is proven. Reading pages by hand is fine. A scraper is
both a terms violation and the kind of thing that gets a seller account
associated with it closed.
