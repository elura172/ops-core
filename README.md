# Ops Core

Dry-run-first commerce operations core. One control plane, two pipelines:

- **TikTok Shop** — an authorized integration, documented below.
- **Amazon private label** — demand screening, unit economics and launch
  budgeting in `app/amazon/`, mapped against this system in
  `docs/amazon-pipeline-map.md`.

```bash
python scripts/scan.py "bread box" "plant mister"   # search, triage, shortlist
python scripts/prefilter_batch.py export.txt        # structural triage, no API
python scripts/budget.py --units 300 --fob 7.50 --price 31.95
python scripts/screen_candidate.py --example        # full screen on one product
```

Both share the same spine: every consequential action becomes a review item, a
human approves it, and an allowlisted executor performs it exactly once. The
sections below describe that spine as the TikTok pipeline exercises it; the
Amazon pipeline reuses it unchanged.

## Safety defaults

- No proxy rotation, fingerprint spoofing, stealth browser automation, or scraping bypasses.
- No live API mutations until the official sandbox flow and request signing are implemented and tested.
- No buyer PII in logs or trend datasets.
- Product creation and fulfillment mutations require explicit application-level approval.
- Browser automation is disabled by default; any UI adapter must be human-supervised and separately approved.

## Operator authentication

### Review desk

Open `/dashboard` on your running Ops Core service to inspect proposed actions,
approve or reject them, and execute approved actions. Use your configured operator
token to connect; it remains in tab memory and is cleared on disconnect or reload.
Use HTTPS when accessing a remote deployment.

The dashboard uses the existing role permissions. Reviewers see decision controls;
operators see execution controls; admins have both. Approval does not execute an
action. Execution requires a separate confirmation and still passes the adapter
gates. Previews show the complete proposed payload, decision history, and stored
result. They can contain customer correspondence, so only authenticated readers
can retrieve them, and responses carry `Cache-Control: no-store`.

The empty queue is intentional: workflows must submit review items to the configured
store. This dashboard does not yet create listings or connect a storefront. The
client mode label describes the TikTok client; publishing and email adapters may
still be simulated or disabled independently. Failed outcomes require reconciliation
and a fresh proposal rather than a retry button.

### Access roles

The review endpoints are the human-in-the-loop control, so they are authenticated
and fail closed. `OPS_TOKENS` maps SHA-256 token digests to named principals:

```
OPS_TOKENS="<sha256>:ada:reviewer,<sha256>:grace:operator"
```

| Role       | `review:read` | `review:decide` | `review:execute` |
|------------|:-------------:|:---------------:|:----------------:|
| `reviewer` | yes           | yes             | no               |
| `operator` | yes           | no              | yes              |
| `admin`    | yes           | yes             | yes              |

Generate a digest with `python -m app.api.security`. Only digests are configured,
so the environment file never holds a usable credential. Reviewer identity is read
from the bearer token, never from the request body. If `OPS_TOKENS` is empty every
review endpoint answers `503` rather than serving unauthenticated — an unconfigured
deployment is a fault, not open access.

Approved actions execute at most once. An item moves `APPROVED → EXECUTING →
EXECUTED`, a replayed call returns the stored result without re-invoking the
client, and a failure is terminal: the outcome of a partial mutation is unknown,
so it requires a fresh approval rather than an automatic retry.

That guarantee is durable, not per-process. `REVIEW_DB` and `ORDER_DB` each
select a backend — usually the same Postgres DSN, since both schemas live on
one migration spine:

- a `postgresql://` DSN opens `PostgresReviewStore` / `PostgresOrderRepository`
  — the multi-worker backends, and what `docker-compose.yml` deploys. Schema is
  owned by forward-only SQL migrations in `migrations/`, applied by
  `python -m app.db.migrate <dsn>` (and automatically before a store opens).
  The runner records each migration in `schema_migrations`, applies each file
  in its own transaction, serializes concurrent runners with an advisory lock,
  and refuses drift between the table and the files on disk.
- a filesystem path opens the SQLite equivalents, for single-process
  deployments.

For Compose, copy `.env.example` to `.env` and set `POSTGRES_PASSWORD`.
The example derives the required `POSTGRES_DSN` from it, and Compose passes that
same DSN to both stores. Neither value has an in-repository default.

In both, the claim is a conditional `UPDATE ... WHERE status = 'APPROVED'`, so a
restart, a replay, or a second worker racing the same approval cannot run the
mutation twice. That is not an aspiration; it is load-tested — see
[Measured behavior](#measured-behavior) — and `tests/test_review_contention.py`
barrier-races 12 workers against one approval and then re-runs the race with the
conditional removed to prove the harness catches the resulting double execution.
The audit trail — who approved, who executed — is written in the same
transaction as the state change it describes, in the `audit_events` table —
that table, not the in-memory `AuditLog`, is the durable trail. An `EXECUTING`
item abandoned by a crash is recovered to `FAILED` at startup, which means a
human re-approves; recovery never retries on its own.

The in-memory `ReviewQueue` implements the same contract for development and
tests, but its state is per-process and is not shared between workers.

## Order webhooks

Webhook delivery is neither ordered nor exactly-once, so `/webhooks/orders`
classifies each event against local state instead of assuming adjacency:

| Situation                                   | Outcome       | HTTP |
|---------------------------------------------|---------------|------|
| Normal or skipped-ahead transition           | `applied`     | 200  |
| Same status we already hold                  | `unchanged`   | 200  |
| `event_id` already processed                 | `duplicate`   | 200  |
| Event we are already past                    | `stale`       | 200  |
| Unreachable in either direction              | `quarantined` | 202  |
| Bad signature / malformed / storage failure  | —             | 401 / 400 / 500 |

Forward jumps fast-forward and log the states that were never observed; nothing is
silently discarded. A storage failure returns `500` so the platform retries.

Conflicting events are stored in `quarantined_events` and surfaced at
`GET /orders/quarantined` (needs `review:read`); `POST
/orders/quarantined/{event_id}/resolve` closes one (needs `review:decide`).

An order first seen mid-lifecycle has an estimated `created_at`, so it is excluded
from the fulfillment SLA rather than measured against a timestamp we inferred. The
SLA covers time-to-dispatch, so anything already shipped is out of scope too.

Both durable order repositories preserve `created_at_estimated` and expose
`active()` for the watchdog. Existing rows are conservatively marked estimated
on upgrade because their timestamp provenance was not stored. This supplies the
read path; it does not schedule watchdog runs or persist alert deduplication.

Every event is folded inside a single `BEGIN IMMEDIATE` transaction, so the
duplicate check and the write cannot interleave across threads or processes.

Logs are one JSON object per line and carry the `X-Correlation-Id` of the request.
Only identifiers, statuses, and counts may be logged — never payloads or buyer data.

## Going live

`LaunchGate` runs in `create_app` before a live client can be constructed, so an
unattested deployment fails **to boot** rather than failing at its first mutation.
Live mode needs all of:

| Control | How it is satisfied |
|---|---|
| `DRY_RUN=false` | intent |
| `LIVE_ENABLED=true` | a second deliberate act, so one stray flag is not enough |
| `secrets_configured` | **derived** — every `TTS_*` value present |
| `approval_required` | **derived** — `OPS_TOKENS` configured, since approval is only a control if the reviewer can be authenticated |
| `signing_verified` | attested via `LAUNCH_SIGNING_VERIFIED` |
| `sandbox_passed` | attested via `LAUNCH_SANDBOX_PASSED` |
| `pii_controls_verified` | attested via `LAUNCH_PII_CONTROLS_VERIFIED` |

`LIVE_ENABLED` and the `LAUNCH_*` flags are inert while `DRY_RUN=true`; the
checklist is only consulted when live mode is actually requested.

Derived checks are not self-declarable. The three attestations are, deliberately —
nothing in the process can verify that a Partner Center sandbox run happened, so
they record a human's claim and the audit trail records who deployed it.

Publishing has its own switch. An approved `publish` review stays blocked until
`PUBLISH_ENABLED=true`, and being blocked does **not** consume the approval: no
mutation was attempted, so the operator flips the flag and executes the same item
rather than going back for a re-review.

## Agent pipeline

`agent → review → execute` is closed: every operation the agent layer can submit
(`publish`, `send_customer_response`) has an executor handler, so an approval never
dead-ends. Both route through adapters that are dry-run or disabled by default.

Customer-response reviews hold the recipient and body, because a human cannot
approve a message they cannot read. That content lives in `REVIEW_DB` and nowhere
else — it is never logged and never returned in an execution result.

## Measured behavior

Numbers from `python -m scripts.load_review_queue`, 2026-08-06, on an Apple
Silicon laptop (M-series, macOS 25.5), Postgres 17.10 on localhost, 8 workers
each holding its own connection pool, 1,000 approved items, full
`submit → decide → claim → execute → complete` per item with the audit row in
the same transaction:

| backend  | executions/s | p50   | p95   | p99   | max    |
|----------|-------------:|------:|------:|------:|-------:|
| postgres |        1,418 | 5.4ms | 7.7ms | 9.8ms | 13.8ms |
| sqlite   |        2,788 | 0.2ms | 0.3ms | 37.6ms | 354.8ms |

SQLite is faster at p50 and an order of magnitude worse at the tail — its
single-writer lock queues whole transactions behind the slowest fsync, which is
exactly why the multi-worker deployment is Postgres.

Contention, same harness: 50 approved items, 12 barrier-synchronized workers
racing each one (600 execute attempts total, per backend). Result: **50 side
effects** — every item executed exactly once; all 550 losers either replayed
the stored result or were refused mid-flight. The inverse is also tested:
strip `AND status = 'APPROVED'` from the claim and the same race double-fires
(5/5 rounds failed when we did exactly that against the real store), so the
green run measures a guard that is provably load-bearing.

Order-event ingest, same harness: 250 orders × 4 lifecycle events each
(PAID, SHIPPED, DELIVERED, plus a replayed PAID), shuffled into a single
stream and folded by 8 workers — webhook delivery is neither ordered nor
exactly-once, so this is the shape of real traffic. Every run must end with
all 250 orders on `DELIVERED` and every replay reported `duplicate`, or the
harness aborts:

| backend  | events/s | p50   | p95   | p99   | max     |
|----------|---------:|------:|------:|------:|--------:|
| postgres |    3,996 | 2.0ms | 3.0ms | 4.8ms |   7.6ms |
| sqlite   |    6,360 | 0.1ms | 0.2ms | 0.5ms | 147.3ms |

The order pipeline's two Postgres guards are proven load-bearing the same way
as the review claim (`tests/test_order_contention.py`): drop the
`SELECT ... FOR UPDATE` on the order row and a raced stale `PAID` overwrites
`SHIPPED`; drop the rowcount check on the `processed_events` insert and a
raced replay stops reporting `duplicate`. Both removals were also applied to
the real repository during development and made the corresponding real tests
fail before being reverted.

The stale-claim alert in that run actually fired — a claim was deliberately
abandoned, recovery moved it to `FAILED`, and the evaluator paged:

```json
{"event": "alert.fired", "level": "ERROR", "logger": "ops_core.alerts",
 "rule": "StaleExecutionRecovered", "severity": "page", "value": 1.0,
 "message": "a worker died holding an EXECUTING claim; the mutation outcome is unknown and a human must reconcile before re-approving",
 "ts": "2026-08-07T01:20:49.264739+00:00"}
```

Reproduce with:

```bash
python -m scripts.load_review_queue sqlite
python -m scripts.load_review_queue postgres postgresql://ops@127.0.0.1:5432/ops_core
```

## Checks

```bash
pytest -q                          # plus OPS_TEST_POSTGRES_DSN=... for the Postgres suite
ruff check . && ruff format --check .
mypy            # strict, configured in pyproject
```

CI runs all four on every push and PR, then builds the image, boots it, and
asserts that an unattested live boot **fails** — the launch gate has to break the
container, not degrade quietly.

## Pricing

`target_price` is fee-aware. `PricingModel` carries commission, transaction fees,
VAT, a refund reserve, and shipping, because marking cost up by the target margin
alone is how a listing that looks 50% profitable sells at a fraction of it — at
the default rates, £10 of cost needs a £28.85 list price to net 50%, not £20.00.
**The default rates are placeholders**; confirm them for your category and market.

## AI copy generation

`OpenAIResponsesCopyProvider` is the only outbound model call. It is deliberately
synchronous — it runs in workers and agents, not request handlers. If you ever call
it from a route, wrap it in `run_in_threadpool`.

- **Retries** are bounded and jittered, and only for retryable statuses
  (408/409/425/429/5xx) plus timeouts and network errors. A 400 or 401 is a request
  to fix, not one to resend, so it fails immediately.
- **`Retry-After` is honoured**, capped by the policy's `max_delay`.
- **Spend is capped** by `UsageLimit` (calls and tokens, enforced *before* each
  request; retries count). Defaults are 100 calls / 250k tokens **per provider
  instance** — the ledger is not shared between workers or across restarts, and a
  provider constructed per call would enforce nothing. Build one and reuse it, or
  pass a shared `UsageLedger`. Limits are in tokens
  rather than currency because a hardcoded stale price is worse than no price.
  The call cap is exact; the token cap can overshoot by one call, because a
  request's token cost is not known until its response arrives. Set it below the
  figure you actually cannot exceed.
- **Supplier facts are untrusted.** They are stripped of control characters, capped
  in count and length, and sent as JSON values with an instruction to treat them as
  data. That reduces prompt-injection surface; it does not eliminate it.
- **Generated copy is policy-gated inside the provider**, so copy carrying a
  restricted health or performance claim raises `PolicyViolation` rather than
  relying on the caller to remember to check. `LocalCopyProvider` enforces the same
  gate — a restricted claim in a supplier fact is still a restricted claim.
- `base_url` must be https (except `localhost`) and may not embed credentials.
  Errors never include the response body or the API key.

The default model string is a placeholder. Confirm it against the provider's
current model list before relying on it.

## Metrics and alerting

`/metrics` (read-authenticated) serves Prometheus text: execution counts by
outcome (`completed`, `failed`, `replayed`, `claim_rejected`), execution
latency, and stale-claim recoveries. Two alerts evaluate in-process on the
rising edge of their counters — `ReviewExecutionFailed` and
`StaleExecutionRecovered`, both severity `page`, both meaning a human must
reconcile before anything is re-approved. Firings are structured
`alert.fired` log events and are listed at `/alerts`; the same conditions are
expressed as Prometheus rules in `observability/prometheus-alerts.yml` for
deployments with a real scraper. The StaleExecutionRecovered firing in
[Measured behavior](#measured-behavior) is from the recorded load run, not a
hypothetical; ReviewExecutionFailed shares the mechanism and its rising-edge
behavior is under test.

`send_ntfy` refuses to publish to an unauthenticated topic. On public ntfy.sh a
topic is only as private as it is unguessable, and SLA alerts carry order IDs. Set
`NTFY_TOKEN`, point `NTFY_SERVER` at a self-hosted instance, or pass
`allow_unauthenticated=True` to accept that anyone with the topic can read them.

`HimalayaMailer` passes the message body over stdin, never argv — process
arguments are world-readable via `ps` and this one carries buyer correspondence.

## Not yet done

The `TikTokClient` is deliberately a boundary rather than a fake implementation.
Current API versions, scopes, signatures, categories, warehouse IDs, and shipping
providers must be obtained from TikTok Shop Partner Center and tested in its
sandbox before enabling live calls. `app/api/signing.py` is an unverified
canonicalization scaffold and is not yet wired into the client — which is exactly
what `LAUNCH_SIGNING_VERIFIED` is attesting to, so do not set it until it is.
