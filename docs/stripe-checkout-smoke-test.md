# Sandbox Checkout smoke test — 2026-10-18

Preview: https://ops-core-8rnf94h6i-eluras-projects-ad12d1e1.vercel.app

Verified in Chrome using a disposable example.com workspace and Stripe's 4242 test card:

1. Signup creates an inactive workspace; sample catalogue drafts are available.
2. The subscription button opens Stripe Checkout for CAD 50/month, in Sandbox mode.
3. Completing Checkout returns to `/dashboard?billing=success` with login instructions.
4. Logging back into the same workspace shows paid onboarding and Manage subscription.
5. Stripe automatically delivered `checkout.session.completed`, `invoice.paid`, and
   `customer.subscription.created`, each with HTTP 200 at 18:35:59 UTC.

Fixes made during the test:

- Migration 0011 advances the tenant SERIAL sequence beyond explicitly seeded IDs
  without rewinding an already advanced sequence. Previously signup collided with tenant 1.
- Free accounts now see the subscription button; it was always hidden.
- Checkout success/cancellation returns to login instead of the original signup URL.

Validation: 23 dashboard/billing tests passed; 2 sequence regression cases passed
against an isolated local PostgreSQL instance; dashboard JavaScript syntax check passed.

Remaining launch work: Checkout displays account branding CPHI-GEO; returning from
Checkout requires login. This proves the successful sandbox path, not live billing,
declined-payment/3DS flows, renewal/cancellation, or customer-portal behavior.

## Customer portal and cancellation follow-up

- Manage subscription opened Stripe's test customer portal for this workspace.
- Portal cancellation scheduled the end for November 18, 2026. A fresh login
  retained paid onboarding and Manage subscription during the remaining paid period.
- The same disposable subscription was then ended immediately in the sandbox
  dashboard to exercise the terminal state without waiting a month.
- `customer.subscription.updated` returned 200 at 20:20:59 UTC;
  `customer.subscription.deleted` returned 200 at 20:21:55 UTC.
- A fresh login returned to free catalogue preview with Choose a subscription;
  paid onboarding and Manage subscription were no longer shown.

The disposable test subscription is now canceled. No real charge was made.
This verifies immediate termination and scheduled-cancellation access retention;
it does not simulate the passage of time to the scheduled end date.
