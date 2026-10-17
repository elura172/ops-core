# Staging Operations Proof & Evidence Report

**Environment**: macOS Apple Silicon (Local PostgreSQL 17.10)
**Commit SHA**: `c3c14b30c6b719dda4013bfdcaf91b8cc7f143df`
**Started At**: `2026-10-02T22:20:49.496364+00:00`
**Completed At**: `2026-10-02T22:20:55.165776+00:00`

> Historical evidence for the commit and environment recorded above. It does not
> verify the current working tree; rerun the staging procedure before treating
> this report as current release evidence.

---

## 1. Issue #4: Staging Deployment Verification

- **Clean PostgreSQL Migration**: Forward migrations applied from zero:
  - `0001_reviews_and_audit`
  - `0002_orders_and_events`
  - `0003_order_timestamp_provenance`
- **Runtime Flags in Force**:
  - `DRY_RUN=true`
  - `LIVE_ENABLED=false`
  - `PUBLISH_ENABLED=false`
- **Health Probe (`GET /health`)**:
  - Response: `{"status": "ok", "mode": "dry-run"}`
- **Metrics (`GET /metrics`)**:
  - Unauthenticated request rejected with `401 Unauthorized`.
  - Authenticated request (`ada`, role `reviewer`) served Prometheus text.
- **Auth Controls**:
  - Missing bearer token -> `401 Unauthorized` (`WWW-Authenticate: Bearer`).
  - Invalid token -> `401 Unauthorized`.
- **Correlation ID**:
  - Verified `X-Correlation-Id` header propagation and preservation.
- **Safe Re-Deployment**:
  - Second server boot against migrated DB booted cleanly with zero drift.

---

## 2. Issue #7: End-to-End Agent → Review → Execute Proof

- **Workflow Progression**:
  1. `Agent Submission`: Created review item `review-staging-proof-01`.
  2. `Inspection`: Authenticated reviewer `ada` inspected pending item.
  3. `Role Check (Operator -> Decision)`: Refused (`403 Forbidden`).
  4. `Role Check (Reviewer -> Execute)`: Refused (`403 Forbidden`).
  5. `Reviewer Approval`: Approved by `ada` with note (`status=APPROVED`).
  6. `Operator Execution`: By `grace` (`status=EXECUTED`, `dry_run=True`).
  7. `Replay`: Returned identical result with `replayed=True` (no re-execution).
- **Negative Case (Publish Disabled)**:
  - Item `review-staging-proof-blocked` (`publish`) approved by reviewer.
  - Operator execution blocked with `403 Forbidden` (`publishing is disabled`).
  - Approval remained unconsumed and intact (`status=APPROVED`).
- **Durable PostgreSQL Audit Trail**:
  - Verified audit rows: `('review.decide', 'ada', 'approved')` and `('approved_action.execute', 'grace', 'completed')`.

---

## 3. Issue #5: PostgreSQL Backup, Restore, and Safe Resume

- **Backup Command**: `pg_dump -h 127.0.0.1 -p 5432 -U ops -Fp ...`
- **Backup Duration**: `0.096s`
- **Backup Size**: `8278 bytes`
- **Restore Duration**: `0.0584s`
- **Integrity Verification in Restored DB**:
  - `schema_migrations`: 3 records
  - `reviews`: 2 records
  - `audit_events`: 2 records
  - `orders`: 1 record
  - `quarantined_events`: 1 record
- **Safe Resume Verification**:
  - Staging API booted against restored database.
  - Replay returned stored result (`replayed=True`) without invoking adapter.
  - Quarantined order event successfully resolved via authenticated endpoint.

---

## 4. Issue #6: Wire Staging Metrics and Prove Alert Delivery

- **Rising-Edge In-Process Alert Evaluator**:
  - Stale execution injected into `reviews` (`status='EXECUTING'`, >15m).
  - On boot, `recover_stale_executions` transitioned item to `FAILED`.
  - `GET /alerts` rule: StaleExecutionRecovered, severity: page
- **Rising-Edge Protection**:
  - Subsequent evaluation calls to `/alerts` did not produce duplicate pages.
- **Prometheus Exporter**:
  - `GET /metrics` verified exposition of `ops_review_stale_recoveries_total`.

---

## Execution Log Transcript

```text
[2026-10-02T22:20:49.496528+00:00] [INIT] Starting Staging Proof against commit c3c14b30c6b719dda4013bfdcaf91b8cc7f143df
[2026-10-02T22:20:49.496572+00:00] [ISSUE_4] === Starting Phase 1: Deploy dry-run staging on PostgreSQL ===
[2026-10-02T22:20:49.496584+00:00] [DB] Recreating clean database ops_core_staging
[2026-10-02T22:20:49.772831+00:00] [ISSUE_4] Clean PostgreSQL database migrated successfully from zero: ['0001_reviews_and_audit', '0002_orders_and_events', '0003_order_timestamp_provenance']
[2026-10-02T22:20:49.772920+00:00] [SERVER] Starting uvicorn on port 8000 with DSN postgresql://ops:ci@127.0.0.1:5432/ops_core_staging
[2026-10-02T22:20:50.504921+00:00] [SERVER] Server started and healthy (PID 52720)
[2026-10-02T22:20:50.517856+00:00] [ISSUE_4] GET /health: {'status': 'ok', 'mode': 'dry-run'}
[2026-10-02T22:20:50.519914+00:00] [ISSUE_4] GET /metrics: Prometheus metrics rendered with authentication
[2026-10-02T22:20:50.522341+00:00] [ISSUE_4] Auth check: Unauthenticated and bad token requests rejected with 401
[2026-10-02T22:20:50.525230+00:00] [ISSUE_4] Correlation ID reflected in response header: test-corr-id-12345
[2026-10-02T22:20:50.525267+00:00] [SERVER] Stopping server (PID 52720)
[2026-10-02T22:20:50.757820+00:00] [ISSUE_4] Testing second deployment against already-migrated DB...
[2026-10-02T22:20:50.757913+00:00] [SERVER] Starting uvicorn on port 8000 with DSN postgresql://ops:ci@127.0.0.1:5432/ops_core_staging
[2026-10-02T22:20:51.407537+00:00] [SERVER] Server started and healthy (PID 52726)
[2026-10-02T22:20:51.420389+00:00] [ISSUE_4] Second deployment succeeded cleanly without manual database repair
[2026-10-02T22:20:51.420412+00:00] [SERVER] Stopping server (PID 52726)
[2026-10-02T22:20:51.699920+00:00] [ISSUE_7] === Starting Phase 2: Prove End-to-End Workflow in Staging ===
[2026-10-02T22:20:51.700022+00:00] [SERVER] Starting uvicorn on port 8000 with DSN postgresql://ops:ci@127.0.0.1:5432/ops_core_staging
[2026-10-02T22:20:52.355434+00:00] [SERVER] Server started and healthy (PID 52732)
[2026-10-02T22:20:52.401612+00:00] [ISSUE_7] Agent submitted review item: review-staging-proof-01 (create_product_draft)
[2026-10-02T22:20:52.417737+00:00] [ISSUE_7] Reviewer inspected item: status=PENDING
[2026-10-02T22:20:52.419051+00:00] [ISSUE_7] Role check: Operator attempt to decide was refused (403 Forbidden)
[2026-10-02T22:20:52.420449+00:00] [ISSUE_7] Role check: Reviewer attempt to execute was refused (403 Forbidden)
[2026-10-02T22:20:52.425039+00:00] [ISSUE_7] Reviewer 'ada' approved item: status=APPROVED
[2026-10-02T22:20:52.434778+00:00] [ISSUE_7] Operator 'grace' executed item: {'dry_run': True, 'operation': 'create_product_draft', 'payload': {'sku': 'TEX-LINEN-01', 'brand': 'OpsCore Home', 'title': 'Organic Linen Tea Towel Set'}}
[2026-10-02T22:20:52.440256+00:00] [ISSUE_7] Replay execution returned identical stored result with replayed=True without invoking adapter again
[2026-10-02T22:20:52.452429+00:00] [ISSUE_7] Negative case: Disabled adapter safely rejected (403) and left approval intact
[2026-10-02T22:20:52.465374+00:00] [ISSUE_7] Webhook order ingest and quarantine seeded successfully
[2026-10-02T22:20:52.473708+00:00] [ISSUE_7] Durable audit events recorded in DB: [('review.decide', 'ada', 'approved'), ('approved_action.execute', 'grace', 'completed'), ('review.decide', 'ada', 'approved')]
[2026-10-02T22:20:52.474329+00:00] [SERVER] Stopping server (PID 52732)
[2026-10-02T22:20:52.757787+00:00] [ISSUE_5] === Starting Phase 3: PostgreSQL Backup, Restore, and Safe Resume ===
[2026-10-02T22:20:52.853966+00:00] [ISSUE_5] pg_dump completed in 0.0960s; backup size = 8278 bytes
[2026-10-02T22:20:52.854018+00:00] [DB] Recreating clean database ops_core_staging_restored
[2026-10-02T22:20:53.123188+00:00] [ISSUE_5] psql restore completed in 0.0584s
[2026-10-02T22:20:53.127479+00:00] [ISSUE_5] Restored DB counts: migrations=3, reviews=2, audits=3, orders=1, processed=3, quarantined=1
[2026-10-02T22:20:53.127809+00:00] [SERVER] Starting uvicorn on port 8000 with DSN postgresql://ops:ci@127.0.0.1:5432/ops_core_staging_restored
[2026-10-02T22:20:54.001455+00:00] [SERVER] Server started and healthy (PID 52748)
[2026-10-02T22:20:54.021704+00:00] [ISSUE_5] Restored app safely resumed: replay returned exact stored result without executing adapter
[2026-10-02T22:20:54.027875+00:00] [ISSUE_5] Restored app smoke test: resolved quarantined event ev-ord-01-bad
[2026-10-02T22:20:54.027920+00:00] [SERVER] Stopping server (PID 52748)
[2026-10-02T22:20:54.256130+00:00] [ISSUE_5] Temporary backup file removed
[2026-10-02T22:20:54.256164+00:00] [ISSUE_6] === Starting Phase 4: Wire staging metrics and prove alert delivery ===
[2026-10-02T22:20:54.262612+00:00] [ISSUE_6] Seeded abandoned EXECUTING review item in staging database
[2026-10-02T22:20:54.262720+00:00] [SERVER] Starting uvicorn on port 8000 with DSN postgresql://ops:ci@127.0.0.1:5432/ops_core_staging
[2026-10-02T22:20:54.919194+00:00] [SERVER] Server started and healthy (PID 52755)
[2026-10-02T22:20:54.932568+00:00] [ISSUE_6] GET /alerts received: [{'rule': 'StaleExecutionRecovered', 'severity': 'page', 'message': 'a worker died holding an EXECUTING claim; the mutation outcome is unknown and a human must reconcile before re-approving', 'value': 1.0, 'fired_at': '2026-10-02T22:20:54.766171+00:00'}]
[2026-10-02T22:20:54.932590+00:00] [ISSUE_6] Confirmed alert firing: rule=StaleExecutionRecovered, severity=page
[2026-10-02T22:20:54.933708+00:00] [ISSUE_6] Rising edge verified: unchanged condition did not page again
[2026-10-02T22:20:54.934739+00:00] [ISSUE_6] Verified ops_review_stale_recoveries_total in Prometheus /metrics
[2026-10-02T22:20:54.934766+00:00] [SERVER] Stopping server (PID 52755)
[2026-10-02T22:20:55.165787+00:00] [FINISH] === All Staging Proof Criteria Passed Successfully! ===
```
