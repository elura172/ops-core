# Handoff for Gemini — custom bookshop website

Build the website side of the Ops Core connection described in this folder. Ops Core's versioned routes and signed publication sender are now implemented locally. Read `STAGING.md` for current status, configuration/key mapping and remaining limits. Use the mock client until the actual staging backends pass their shared checks. Keep payments disabled and preserve existing website design work.

Read `README.md`, `openapi.json`, and `examples/*.json` before implementing. The OpenAPI operation-level `servers` distinguish the website receiver from Ops Core; example domains are placeholders.

## Build on the website

1. A backend `PUT /api/ops-core/v1/books/{sku}` receiver. Verify signatures on raw bytes, bind credentials to this client connection, and persist complete snapshots/revisions transactionally. Make duplicate and out-of-order requests safe. Withdrawn copies remain tombstones.
2. Public catalogue/product pages from that local read model. Purchase controls require published + available + quantity 1. Use Ops Core's SKU and online CAD price. Missing edition, publisher, date or photo stays unknown; never manufacture catalogue details. Keep copy descriptions plain text.
3. A server-side integration client for reservations, release, paid/cancelled events, and event-status checks. Follow the signing format and durable retries in the README. Use a mock implementation behind the same interface while Ops Core is unfinished.
4. Checkout that reserves stock before payment, shows expiry, and never relies on cached inventory as proof that a copy can be sold. Verify payment on the server. Do not enable real payment until atomic claims work across Ops Core's other sale/hold paths too.
5. A durable outbound order-event queue and useful staff status for failed delivery, expired paid reservations, and downstream sync requiring review. A 202 response is not confirmation that all marketplaces have updated.

Keep API keys in backend secrets, never public environment variables/browser code. Store customer contact, payment and shipping details on the website; this v1 contract sends only copy/order/reservation identifiers and agreed book prices.

Suggested configuration names (new design, not existing Ops Core variables):

```text
OPS_CORE_BASE_URL=<staging Ops Core URL>
OPS_CORE_OUTBOUND_KEY_ID=<website-to-Ops-Core key ID>
OPS_CORE_OUTBOUND_SECRET=<server-only random signing secret>
OPS_CORE_INBOUND_KEY_ID=<Ops-Core-to-website key ID>
OPS_CORE_INBOUND_SECRET=<different server-only random signing secret>
OPS_CORE_CONNECTION_MODE=mock
```

Use the site's existing backend conventions for routing, database, jobs and configuration. Add migrations and meaningful integration tests; do not replace the architecture just to match a sample stack. Record exact setup commands in your website repo.

## Demonstrate before handoff

- An available example book renders correctly without a photo; reviewed updates replace it.
- A held/sold snapshot disables purchase; unpublication removes the public page and invalidates caches.
- An older available snapshot cannot revive a withdrawn/sold copy.
- Signature and idempotency tests pass, including changed content under an existing event ID.
- Mock checkout covers success, competing reservation, price change, expiry, API outage, duplicate paid events, and cancellation without automatic relisting.
- Secrets and customer records are absent from public responses and browser bundles.

Return the website repo path, stack, receiver URL, configuration names, completed tests, and remaining assumptions. Share secret values privately with the owner, never in source or this handoff. Do not claim a live connection until both implementations pass the shared acceptance cases.
