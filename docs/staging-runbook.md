# Staging Operations Runbook & Evidence Index

This runbook is the operational guide for deploying, operating, and verifying the `ops-core` dry-run staging environment on PostgreSQL. It documents tested procedures and provides an evidence index linking the verification artifacts produced for the **Staging Proof** milestone ([#4](https://github.com/elura172/ops-core/issues/4), [#5](https://github.com/elura172/ops-core/issues/5), [#6](https://github.com/elura172/ops-core/issues/6), [#7](https://github.com/elura172/ops-core/issues/7), [#8](https://github.com/elura172/ops-core/issues/8)).

> [!CAUTION]
> **STAGING SAFETY ENFORCEMENT**:
> `DRY_RUN` must always be `true`. **NEVER** set `LIVE_ENABLED=true` or any `LAUNCH_*` attestations (`LAUNCH_SIGNING_VERIFIED`, `LAUNCH_SANDBOX_PASSED`, `LAUNCH_PII_CONTROLS_VERIFIED`) in staging. Live adapters are unverified scaffolds; setting live flags bypasses safety gates designed to protect against unintended commerce mutations.

---

## 1. Architecture and Trust Boundaries

`ops-core` staging operates under strict role separation and dry-run boundaries:

```
[ External / Agent ]
        │
        ▼ (Direct DB submission or Webhook ingest)
┌────────────────────────────────────────────────────────┐
│ PostgreSQL 17 (Durable Stores)                         │
│  ├── reviews (status, payloads, decision, execution)   │
│  ├── audit_events (sequence, actor, action, outcome)   │
│  ├── orders & processed_events                         │
│  └── quarantined_events                                │
└────────────────────────────────────────────────────────┘
        ▲                         ▲
        │                         │
┌───────┴───────────────┐ ┌───────┴───────────────┐
│ Reviewer Principal    │ │ Operator Principal    │
│ (ada: review:decide,  │ │ (grace: review:execute│
│  review:read)         │ │  review:read)         │
│                       │ │                       │
│ Endpoint: /reviews    │ │ Endpoint: /reviews    │
│           /dashboard  │ │           /execute    │
└───────────────────────┘ └───────────────────────┘
```

- **Authentication Boundary**: All operator and reviewer endpoints require HTTP Bearer authentication against a pre-hashed token registry (`OPS_TOKENS`). Missing or invalid tokens are rejected with `401 Unauthorized`.
- **Role Boundary**:
  - `reviewer` holds `review:read` and `review:decide`. Cannot execute mutations (`403 Forbidden`).
  - `operator` holds `review:read` and `review:execute`. Cannot approve or reject review items (`403 Forbidden`).
- **Adapter Boundary**: In staging, `TikTokClient.dry_run=True`. External network calls to TikTok Shop or email providers are intercepted or mocked in dry-run mode. `PUBLISH_ENABLED=false` blocks publication requests safely.

---

## 2. Prerequisites and Access

Staging infrastructure requires:
- **Host**: macOS (Apple Silicon) or Linux (`x86_64` / `aarch64`)
- **Python**: 3.11+ (tested with system Python 3.13 via `uv`)
- **Database**: PostgreSQL 17.x (tested on PostgreSQL 17.10)
- **Tooling**: `uv`, `psql`, `pg_dump`, `curl`

To verify database accessibility:
```bash
pg_isready -h 127.0.0.1 -p 5432 -U ops
```

---

## 3. Secret and Token Handling

Never store plaintext tokens in configuration files or repositories. `OPS_TOKENS` accepts only SHA-256 digests:

```text
OPS_TOKENS=<sha256-hex>:<username>:<role>[|<role>],...
```

To generate a token entry:
```bash
python -c 'import hashlib, getpass; t = getpass.getpass("Token: "); print(hashlib.sha256(t.strip().encode()).hexdigest())'
```

Example configuration for staging:
```bash
export REVIEWER_TOKEN="<generated-reviewer-secret>"
export OPERATOR_TOKEN="<generated-operator-secret>"
export ADMIN_TOKEN="<generated-admin-secret>"

export OPS_TOKENS="$(echo -n "$REVIEWER_TOKEN" | shasum -a 256 | cut -d' ' -f1):ada:reviewer,$(echo -n "$OPERATOR_TOKEN" | shasum -a 256 | cut -d' ' -f1):grace:operator,$(echo -n "$ADMIN_TOKEN" | shasum -a 256 | cut -d' ' -f1):root:admin"
export TTS_WEBHOOK_SECRET="<staging-webhook-hmac-key>"
```

---

## 4. First Deployment from an Empty PostgreSQL Database

1. **Create Staging Database**:
   ```bash
   createdb -h 127.0.0.1 -U ops ops_core_staging
   ```

2. **Set Environment Variables**:
   ```bash
   export DRY_RUN=true
   export LIVE_ENABLED=false
   export PUBLISH_ENABLED=false
   export POSTGRES_URL="postgresql://ops:<secret-password>@127.0.0.1:5432/ops_core_staging"
   export ORDER_DB="$POSTGRES_URL"
   export REVIEW_DB="$POSTGRES_URL"
   export USER_DB="$POSTGRES_URL"
   export INVENTORY_DB="$POSTGRES_URL"
   export WANT_LIST_DB="$POSTGRES_URL"
   ```

3. **Apply Forward Migrations**:
   Migrations are automatically discovered and applied in an advisory lock on boot, or applied directly:
   ```bash
   python -c "from app.db.migrate import migrate; migrate('$POSTGRES_URL')"
   ```
   *Verified Applied*:
   - `0001_reviews_and_audit`
   - `0002_orders_and_events`
   - `0003_order_timestamp_provenance`

4. **Boot the API Server**:
   ```bash
   uvicorn app.api.http:create_app --factory --host 127.0.0.1 --port 8000
   ```

5. **Verify Readiness**:
   ```bash
   curl -sf http://127.0.0.1:8000/health
   # Expected: {"status":"ok","mode":"dry-run"}
   ```

---

## 5. Routine Deployment and Rollback

### Routine Deployment
1. Pull target commit SHA.
2. Run automated test suite:
   ```bash
   OPS_TEST_POSTGRES_DSN="$POSTGRES_DSN" pytest -q
   ```
3. Restart API process. Migrations are idempotent and run automatically under a PostgreSQL advisory lock (`42_001`).

### Rollback Procedure
If a deployment fails before or after boot:
1. Terminate running process.
2. Re-point service configuration to the previous known good commit.
3. If database state was corrupted, restore from the snapshot following [Section 8](#8-backup-and-restore).

---

## 6. Migration Procedure and Drift Failure

The migration engine (`app/db/migrate.py`) refuses drift. If a migration is deleted or renamed on disk after having been recorded in `schema_migrations`, the application refuses to boot:
```text
MigrationError: applied migration 0003_order_timestamp_provenance does not match the files on disk; refusing to continue
```
**Resolution**:
- Never modify or delete previously committed migration files.
- Apply database changes exclusively by adding forward-only migrations (`0004_...sql`).

---

## 7. Health, Metrics, Logs, and Correlation IDs

- **Health Probe (`GET /health`)**:
  Publicly accessible; reports `"mode": "dry-run"`.
- **Metrics (`GET /metrics`)**:
  Protected by `review:read` authorization. Serves Prometheus exposition format.
- **Correlation IDs**:
  Every request generates or preserves `X-Correlation-Id`. Log lines carry `correlation_id` in JSON structured fields without logging request bodies, credentials, or buyer PII.

---

## 8. Backup and Restore

### Backup
Run logical snapshot using `pg_dump`:
```bash
pg_dump -h 127.0.0.1 -p 5432 -U ops -Fp -f /srv/backups/ops_core_staging_$(date +%Y%m%d%H%M%S).sql ops_core_staging
```
*Observed Backup Performance*:
- **Size**: ~8.3 KB (staging baseline)
- **Time**: 0.089 seconds

### Restore
1. Create empty target database:
   ```bash
   createdb -h 127.0.0.1 -U ops ops_core_staging_restored
   ```
2. Restore database state:
   ```bash
   psql -h 127.0.0.1 -U ops -d ops_core_staging_restored -f /srv/backups/<backup-file>.sql
   ```
*Observed Restore Performance*:
- **Time**: 0.045 seconds
- **Recovery Time Objective (RTO)**: < 1 minute
- **Recovery Point Objective (RPO)**: Controlled snapshot interval

---

## 9. Alert Delivery and Alert-Response Steps

Two in-process rising-edge alerts evaluate on counters and expose firings at `/alerts`:
1. **`StaleExecutionRecovered`** (`severity: page`):
   - **Trigger**: Worker crashes or dies holding an `EXECUTING` claim older than the recovery threshold.
   - **Action**: Stale watchdog marks item `FAILED`. Operator must investigate logs and reconcile with upstream store before re-approval.
2. **`ReviewExecutionFailed`** (`severity: page`):
   - **Trigger**: An approved mutation encounters an unhandled adapter failure during execution.
   - **Action**: Item is terminal; operator must inspect audit trail and re-approve if appropriate.

Alerts fire once on the rising edge; unchanged counters do not repeatedly page.

---

## 10. End-to-End Dry-Run Workflow

To exercise the standard workflow:
1. **Submit Review Item**:
   ```python
   store.submit(ReviewItem("review-01", "create_product_draft", {"title": "Linen Towel", "sku": "LIN-01"}))
   ```
2. **Inspect via Operator Desk**:
   Navigate to `http://127.0.0.1:8000/dashboard` or call `GET /reviews/review-01`.
3. **Approve (Reviewer)**:
   ```bash
   curl -X POST http://127.0.0.1:8000/reviews/review-01/decision \
     -H "Authorization: Bearer $REVIEWER_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"approve": true, "reason": "Verified specs"}'
   ```
4. **Execute (Operator)**:
   ```bash
   curl -X POST http://127.0.0.1:8000/reviews/review-01/execute \
     -H "Authorization: Bearer $OPERATOR_TOKEN"
   ```
5. **Replay Check**:
   Re-running execution returns the cached result (`"replayed": true`) without re-invoking the adapter.

---

## 11. Stale `EXECUTING` Recovery and Reconciliation

If an execution worker crashes:
1. The row remains in `status = 'EXECUTING'`.
2. On the next restart or watchdog sweep, `recover_stale_executions(older_than)` executes:
   ```sql
   UPDATE reviews SET status = 'FAILED', failure = 'execution did not complete; outcome unknown'
   WHERE status = 'EXECUTING' AND executed_at < cutoff
   RETURNING *;
   ```
3. An audit row is recorded.
4. The item is marked `FAILED` and requires operator manual reconciliation before re-submission.

---

## 12. Credential Rotation

1. **Operator Tokens**:
   - Generate new random token.
   - Compute SHA-256 hash.
   - Update `OPS_TOKENS` environment variable to include both old and new token hashes.
   - Gracefully reload service.
   - Remove retired token hash from `OPS_TOKENS`.
2. **Webhook Secrets**:
   - Update `TTS_WEBHOOK_SECRET` on application and webhook provider simultaneously.

---

## 13. Known Limitations and Live-Launch Blockers

The following items are **explicitly blocked** from live production until fulfilled:
1. **TikTok Shop API Integration**: `TikTokClient` is currently a boundary scaffold. Real HMAC-SHA256 signature canonicalization (`app/api/signing.py`) must be verified in partner sandbox (`LAUNCH_SIGNING_VERIFIED`, `LAUNCH_SANDBOX_PASSED`).
2. **PII Handling Controls**: Customer names, phone numbers, and addresses must be masked according to platform privacy requirements (`LAUNCH_PII_CONTROLS_VERIFIED`).
3. **Publish Enablement**: Publishing remains gated by `PUBLISH_ENABLED=false`.

---

## 14. Evidence Index (Staging Proof Milestone)

All acceptance criteria for the Staging Proof milestone were executed and recorded by `scripts/prove_staging.py`:

| Milestone Issue | Focus Area | Status | Evidence Document |
| :--- | :--- | :--- | :--- |
| **[#4](https://github.com/elura172/ops-core/issues/4)** | Deploy reproducible dry-run staging on PostgreSQL | **Verified** | [`docs/staging-evidence.md#1-issue-4-staging-deployment-verification`](file:///Users/mirrr/ops-core/docs/staging-evidence.md) |
| **[#5](https://github.com/elura172/ops-core/issues/5)** | PostgreSQL backup, restore, and safe resume | **Verified** | [`docs/staging-evidence.md#3-issue-5-postgresql-backup-restore-and-safe-resume`](file:///Users/mirrr/ops-core/docs/staging-evidence.md) |
| **[#6](https://github.com/elura172/ops-core/issues/6)** | Wire staging metrics and prove alert delivery | **Verified** | [`docs/staging-evidence.md#4-issue-6-wire-staging-metrics-and-prove-alert-delivery`](file:///Users/mirrr/ops-core/docs/staging-evidence.md) |
| **[#7](https://github.com/elura172/ops-core/issues/7)** | End-to-end agent → review → execute workflow | **Verified** | [`docs/staging-evidence.md#2-issue-7-end-to-end-agent--review--execute-proof`](file:///Users/mirrr/ops-core/docs/staging-evidence.md) |
| **[#8](https://github.com/elura172/ops-core/issues/8)** | Publish staging operations runbook & evidence index | **Verified** | [`docs/staging-runbook.md`](file:///Users/mirrr/ops-core/docs/staging-runbook.md) |

---
*Generated: 2026-10-02 | Repository: [elura172/ops-core](https://github.com/elura172/ops-core)*
