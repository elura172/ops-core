# Stripe integration plan

## Recommended first release

Treat Ops Core first as a SaaS platform for booksellers: each shop pays Ops Core a recurring subscription, while book sales continue through the shop's own website and sales channels. The current custom-bookshop contract keeps checkout and payment details on the website. Under that flow, Stripe Billing is needed now; Connect for book-sale payments is not needed until Ops Core itself onboards sellers or moves book-sale funds.

The current dashboard shows an `ops-core monthly` product priced at CAD $50 per month, but the visible dashboard account was `spiraloflight.life`. Verify the product and price are in the intended Ops Core Stripe account before wiring the price ID into the app.

## Billing and invoices

- Use Stripe Checkout Sessions in subscription mode for signup. Keep the existing server-side session creation and tenant-to-customer mapping.
- Provide a Customer Portal session so a bookseller can update payment details, cancel, and view subscription invoices without contacting support. A tenant-scoped `/billing/portal` endpoint and active-subscriber dashboard button are now implemented locally.
- Use Stripe's subscription invoices for recurring charges. Add standalone Invoicing API workflows only if booksellers need manual invoices for separate services or orders.
- Persist webhook event IDs for deduplication and make duplicate Checkout submissions idempotent. Continue checking Stripe's current subscription state before changing access.
- Handle checkout completion and asynchronous payment outcomes, subscription lifecycle events, `invoice.paid` / `invoice.payment_failed`, and the refund, dispute, and early-fraud events the service will act on.

## Connect decision

The current code and custom-bookshop contract describe each bookseller's website taking payment and reporting the resulting sale to Ops Core. For that model, defer Connect and avoid making Ops Core a payment intermediary.

If a future version takes book buyers' payments and routes proceeds to booksellers, design Connect separately before implementing it. Decide who is the merchant of record, who handles refunds and disputes, whether Ops Core keeps a fee, and whether funds move immediately or after fulfillment. Use Accounts v2 and Stripe-hosted or embedded onboarding; then select the matching direct, destination, or separate-charges-and-transfers flow. Do not choose a funds-flow pattern until those responsibilities are explicit.

## Tax

- Start with tax on Ops Core's SaaS subscription only. Collect the customer's billing address and select the correct product tax code.
- Enable automatic tax only after the liable business has determined where it must collect tax and has active registrations there. Stripe Tax does not register the business with tax authorities.
- If Connect later handles book-sale payments, decide tax liability per transaction flow. The bookseller and Ops Core might have different tax responsibilities; do not reuse the SaaS tax configuration for book sales by default.

## Current repository review

The existing implementation in `app/api/billing.py` already uses a per-app `StripeClient`, subscription Checkout Sessions, same-origin return-URL validation, signed webhook verification, async payment handling, and current-subscription lookups before granting access. It handles subscription lifecycle events and recurring invoice success/failure. The focused billing suite passes: `8 passed`.

Before enabling subscriptions for customers, add durable webhook-event deduplication, duplicate-checkout protection, and clear recovery/support behavior for past-due and canceled tenants. Connect, standalone invoices, and automatic tax are not currently implemented. Stripe keys and a Stripe price are also not configured in Vercel Production, so checkout is currently unavailable there. The focused billing suite now passes: `10 passed`.

## Safe rollout

1. Confirm the target Stripe account and verify that the CAD $50 monthly price belongs to it.
2. Revoke the test secret key that was pasted into chat. Create a replacement restricted test key and a matching test webhook secret; keep both server-side.
3. Configure the keys and price in Vercel Preview first, then test checkout, webhook retries, renewal, payment failure, cancellation, portal actions, and tenant isolation.
4. Set up the production webhook and live credentials only after the business profile, pricing, tax registrations, support path, and preview acceptance checks are ready.

## Stripe references

- [Design a subscriptions integration](https://docs.stripe.com/billing/subscriptions/design-an-integration)
- [Billing webhooks](https://docs.stripe.com/billing/subscriptions/webhooks)
- [Connect and Accounts v2](https://docs.stripe.com/connect/accounts-v2)
- [Tax for software platforms](https://docs.stripe.com/tax/tax-for-platforms)
- [How invoicing works](https://docs.stripe.com/invoicing/overview)
