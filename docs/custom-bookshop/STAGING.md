# Ops Core staging setup

The Ops Core v1 routes are implemented locally. They have not been deployed or connected to the client's hosted website. Keep the website's payment processing disabled.

## What is implemented

- Signed, tenant-bound reservation, release, paid/cancelled order-event and event-status routes from `openapi.json`.
- Atomic single-copy claims in the inventory database, with all-or-nothing checkout and a fixed 15-minute expiry checked against database time.
- Collector hold claims and incoming Square/AbeBooks/Biblio sales share that local claim ledger. A late external sale freezes a contested copy and invalidates its website reservation. This cannot stop a remote marketplace accepting a sale before its notification reaches Ops Core.
- Durable request deduplication, stock changes, order linkage and publication outbox records in one transaction.
- Approved `publish_custom_site` review operation, monotonic book revisions and withdrawal tombstones.
- Signed HTTPS publishing, bounded retries, durable delivery leases and per-event delivery status.
- `POST /integrations/custom-site/deliveries/run` for an authenticated operator and integration delivery processing in `/api/cron/sync`.

**Not yet verified:** the actual Gemini website receiver, a hosted staging deployment, and end-to-end remote marketplace stock updates. The current Square delister is a stub, and other adapters still need provider-backed validation. Stock jobs targeting those channels currently end in `needs_review`; they are never reported as successful simulated deliveries. Do not enable paid checkout on this evidence alone.

## Configuration

Use a separate staging PostgreSQL database for every Ops Core store. `POSTGRES_URL` can supply the shared DSN. Claims, reservations, orders, receipts and outbox data live in the inventory database. Migration `0009_custom_bookshop.sql` installs the ledger and inventory guards; `0010_public_book_metadata.sql` preserves public copy metadata on PostgreSQL. The store factory runs migrations before serving.

`CUSTOM_SITE_CONNECTIONS` is a JSON array stored in the backend secret manager. Example structure below contains placeholders only. Replace signing secrets with independent random values of at least 32 bytes; never copy real secrets into Git or chat.

```json
[
  {
    "connection_id": "client_bookshop_staging",
    "tenant_id": 1,
    "inbound_key_id": "website_to_ops_staging",
    "inbound_secret": "REPLACE_WITH_RANDOM_WEBSITE_TO_OPS_SECRET",
    "website_url": "https://REPLACE-WITH-WEBSITE-STAGING-HOST",
    "outbound_key_id": "ops_to_website_staging",
    "outbound_secret": "REPLACE_WITH_DIFFERENT_RANDOM_OPS_TO_WEBSITE_SECRET",
    "enabled": true,
    "checkout_enabled": false,
    "delivery_enabled": false,
    "stock_protection_enabled": false,
    "channels": []
  }
]
```

Key mapping to the Gemini website configuration:

| Ops Core | Website |
| --- | --- |
| `inbound_key_id`, `inbound_secret` | `OPS_CORE_OUTBOUND_KEY_ID`, `OPS_CORE_OUTBOUND_SECRET` |
| `outbound_key_id`, `outbound_secret` | `OPS_CORE_INBOUND_KEY_ID`, `OPS_CORE_INBOUND_SECRET` |
| Actual Ops Core staging origin | `OPS_CORE_BASE_URL` |
| Actual website staging origin | Ops Core `website_url` |

The tenant must have an active subscription. Connections default to checkout and delivery disabled. Malformed/ambiguous booleans fail startup. Disabled keys immediately fail authentication after configuration reload. For a short inbound-key rotation overlap, set optional `inbound_previous_keys` to a map of old key IDs to their signing secrets on the same connection. Remove those old keys promptly after rotation; they share the same durable idempotency records.

For controlled staging tests with **no real payments**, enable `stock_protection_enabled`, `delivery_enabled`, and `checkout_enabled` on the staging connection. Match the website's configured connection mode to its actual implementation; do not assume changing a URL alone switches it out of mock mode. Keep staging and production secrets separate.

At startup, pre-existing active collector holds for connected tenants are imported into the shared stock ledger. Conflicting or missing inventory fails closed rather than letting checkout bypass a hold. Reconcile those records before retrying startup.

## Publish one reviewed book

Use existing operator authentication. Propose this operation via `POST /reviews`:

```json
{
  "operation": "publish_custom_site",
  "payload": {
    "connection_id": "client_bookshop_staging",
    "sku": "REPLACE_WITH_REVIEWED_SKU",
    "publication": "published"
  }
}
```

Inspect the review, approve it with `POST /reviews/{review_id}/decision`, then execute it with `POST /reviews/{review_id}/execute`. The proposal includes the real public catalogue details and their digest. If those details or prices change before execution, create a new review. Publishing queues a durable snapshot; it does not mean the website has received it. Trigger `POST /integrations/custom-site/deliveries/run` with an operator token, then verify the actual website page. Use `publication: unpublished` in a new review to withdraw the copy.

The delivery-run endpoint drains up to 25 jobs for the authenticated tenant. Scheduled `/api/cron/sync` drains jobs for configured connections after reconciliation and requires `CRON_SECRET`. The existing Vercel schedule is daily; that is not a prompt stock-delivery service. Configure a frequent authenticated worker for staging and check its runtime budget before production. A drain checks a 25-second time budget between jobs, with up to 12 seconds for an in-flight HTTP attempt. Leave room for reconciliation and database work within the host request limit, or run a dedicated worker. Durable leases permit recovery after interruption.

Only reviewed/public copy fields enter the snapshot. Missing photos remain empty until a durable public HTTPS image URL is stored. Do not expect local image paths to appear on the website. Extra catalogue fields now persist in both inventory backends; public snapshots never contain collector details or internal shelf locations.

## Check the connection

Run every acceptance case in `README.md` against both actual staging backends. The local tests in `tests/test_custom_site.py` cover both SQLite and PostgreSQL when `OPS_TEST_POSTGRES_DSN` points at a disposable test database; those tests **truncate test tables**, so never use a production/staging DSN containing real client data.

```bash
.venv/bin/pytest -q tests/test_custom_site.py
# To include PostgreSQL, set OPS_TEST_POSTGRES_DSN to a disposable test database first.
```

Record website and Ops Core deployment revisions, migration versions, receiver URL, configuration mode, exact test results, and remaining failures in staging evidence. Do not include secrets or customer records.

## Compatibility changes

- `/webhooks/abebooks`, `/webhooks/biblio`, and POST `/webhooks/ebay` now require operator authentication and derive the tenant from that identity. Direct provider pushes need a separately verified provider-signature adapter; polling remains the existing ingestion path. Do not expose these endpoints anonymously to make a provider connection work.
- The global Square signing secret is bound to tenant 1. A caller cannot select a different tenant. OPEN orders no longer count as completed sales, and completed orders need a stable order/event ID.
- The draft `/api/shop/holds` write/delete routes now require reviewer authentication. They are not checkout reservations. The draft `/api/shop/catalog` remains client-specific legacy output; use the versioned publication receiver for the new connection.
- Inventory guards reject ordinary reactivation/deletion of claimed stock. An explicit authenticated operator relist can clear a sold/blocked claim after staff reconcile physical stock and competing orders. A reservation release or cancellation cannot do that.

## Recovery

Inspect `site_outbox` using tenant-scoped internal tooling. `pending` jobs with expired leases can be delivered again under the same stable event ID. `needs_review` means retries were exhausted or the adapter could not confirm a real delivery. After fixing a delivery problem, an operator may reset only the affected job's state/lease/attempt schedule in the database; retain its ID and payload. Never forge a successful receipt or generate a new paid event to conceal an ambiguous timeout.

Disabling a connection stops authentication and outbound delivery; durable claims still protect stock. Reconcile paid orders and active reservations before removing a connection or rolling code back. Do not drop the ledger or bypass its stock guards to clear a checkout error.

## Local verification — 2026-10-16

- Full regression suite with a disposable PostgreSQL 17 database: **520 passed in 17.03 seconds**. No tests skipped in that run. The 39 warnings concern the existing psycopg pool `open` default deprecation.
- New custom-site suite exercises SQLite and PostgreSQL: competing checkout/collector claims, expiry without cron, cancellation, durable replay, restart recovery, storage-failure rollback, signing-key rotation, rate limits, publishing signatures, Retry-After, withdrawn revisions, stale worker leases, authenticated counter holds, collector-to-marketplace transfer and tenant context isolation.
- The actual publishing HTTP sender was tested against an in-process receiver. This verifies signing and receipt handling, not the hosted Gemini website.
- New integration modules, router and tests pass Ruff. Dashboard JavaScript passes syntax checking, and `git diff --check` is clean.
- OpenAPI schemas, all seven sample payloads and handoff links validate.

No production deployment, payment transaction, or real marketplace mutation was performed. The temporary PostgreSQL server was isolated to a private Unix socket with TCP disabled and stopped after verification.
