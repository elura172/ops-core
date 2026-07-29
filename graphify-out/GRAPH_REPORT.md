# Graph Report - /Users/mirrr/tiktok-ops  (2026-07-27)

## Corpus Check
- Corpus is ~19,164 words - fits in a single context window. You may not need a graph.

## Summary
- 818 nodes · 2117 edges · 43 communities (27 shown, 16 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 203 edges (avg confidence: 0.57)
- Token cost: 62,000 input · 3,327 output

## Community Hubs (Navigation)
- Order Webhook Ingestion
- Model Client Failure Taxonomy
- Approved Action Execution
- Agent Orchestration and Audit
- Product Copy Policy Gate
- Deployment Controls and Docs
- Campaign Spend Guardrails
- Ad Media Provenance
- Unit Economics and Experiments
- Trend Discovery Ranking
- Supplier Fulfillment Boundary
- HTTP App Assembly
- Operator Service and Shop Client
- Durable Review Persistence
- In-Process Review Queue
- API Endpoint Tests
- Settings and Token Registry
- Consent-Aware Retention
- Launch Checklist Gate
- Review Row Mapping
- Review Store Contract
- Review Durability Tests
- Fail-Closed Authentication
- Disabled Browser Adapter
- Structured Logging
- Launch Gate Boot Tests
- Agents Package
- AI Package
- Analytics Package
- Customer Support Package
- Persistence Package
- Discovery Package
- Domain Package
- Growth Package
- Application Root
- Media Package
- Ops Package
- Publish Package
- Retention Package
- Review Package
- Suppliers Package
- Project Manifest

## God Nodes (most connected - your core abstractions)
1. `ReviewItem` - 60 edges
2. `ApprovedActionExecutor` - 46 edges
3. `ReviewQueue` - 36 edges
4. `SQLiteReviewStore` - 32 edges
5. `AuditLog` - 29 edges
6. `OrderStatus` - 29 edges
7. `OrderStore` - 27 edges
8. `install()` - 26 edges
9. `provider()` - 25 edges
10. `ProductCopyRequest` - 24 edges

## Surprising Connections (you probably didn't know these)
- `api compose service` --semantically_similar_to--> `Dry-run boot health check step`  [INFERRED] [semantically similar]
  docker-compose.yml → .github/workflows/ci.yml
- `Client` --uses--> `AgentRole`  [INFERRED]
  tests/test_agent_pipeline.py → app/agents/models.py
- `Client` --uses--> `TaskStatus`  [INFERRED]
  tests/test_agent_pipeline.py → app/agents/models.py
- `Client` --uses--> `AgentOrchestrator`  [INFERRED]
  tests/test_agent_pipeline.py → app/agents/orchestrator.py
- `RecordingClient` --uses--> `SQLiteReviewStore`  [INFERRED]
  tests/test_review_store.py → app/db/review_store.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Live launch gating: checklist, gate, CI proof, compose defaults** — readme_launchgate, readme_launch_checklist, readme_derived_vs_attested, _github_workflows_ci_unattested_boot_refusal, _github_workflows_ci_attested_live_boot, docker_compose_safe_default_env [INFERRED 0.85]
- **Human-in-the-loop approval and at-most-once execution flow** — readme_ops_tokens, readme_role_matrix, readme_exactly_once_execution, readme_sqlitereviewstore, readme_audit_events, readme_agent_pipeline [INFERRED 0.85]
- **AI copy generation safety controls** — readme_openairesponsescopyprovider, readme_localcopyprovider, readme_usagelimit, readme_retry_policy, readme_policyviolation, readme_untrusted_supplier_facts [INFERRED 0.85]

## Communities (43 total, 16 thin omitted)

### Community 0 - "Order Webhook Ingestion"
Cohesion: 0.06
Nodes (70): handle_order_webhook(), OrderRepository, Protocol, HTTP status is carried beside the body, never inside it., Framework-neutral webhook handler; never logs or returns raw payloads., WebhookResult, Extract only event/order/status identifiers from a webhook payload., Verify an HMAC-SHA256 webhook signature without logging the body. (+62 more)

### Community 1 - "Model Client Failure Taxonomy"
Cohesion: 0.07
Nodes (72): CopyProviderError, PolicyViolation, RuntimeError, RateLimited, Not retryable: bad request, bad credentials, unusable response., The configured call or token budget is spent; no request was made., Generated copy failed the product policy gate and must not be used., Base for every failure a copy provider can raise. (+64 more)

### Community 2 - "Approved Action Execution"
Cohesion: 0.08
Nodes (47): Register draft-only handlers; outputs become human-review items., register_publish_and_support_agents(), Decision, Reviewer identity is taken from the bearer token, never from the body., CustomerResponse, DryRunMailer, HimalayaMailer, Mailer (+39 more)

### Community 3 - "Agent Orchestration and Audit"
Cohesion: 0.07
Nodes (38): AgentRole, AgentSpec, AgentTask, Handoff, StrEnum, TaskStatus, AgentOrchestrator, Any (+30 more)

### Community 4 - "Product Copy Policy Gate"
Cohesion: 0.11
Nodes (37): copy_to_candidate(), enforce_policy(), LocalCopyProvider, ProductCopy, Deterministic fallback for tests and dry runs; does not call a model., Convert structured output into the existing policy-gated domain model., Return policy reasons; empty means the copy passed., Raise unless the copy passes the gate.      Providers call this before returning (+29 more)

### Community 5 - "Deployment Controls and Docs"
Cohesion: 0.07
Nodes (42): Attested live boot step, check job (lint/format/types/tests), CI workflow, container job (build, boot, gate assertions), Dry-run boot health check step, Negative-test discipline for the launch gate, Unattested live boot must fail step, api compose service (+34 more)

### Community 6 - "Campaign Spend Guardrails"
Cohesion: 0.12
Nodes (21): campaign_url(), can_spend(), Decimal, rank_creatives(), Budget guardrail; callers must persist spend atomically., DisabledSocialChannel, Protocol, Prevents unattended publishing or ad spend until a reviewed adapter is installed (+13 more)

### Community 7 - "Ad Media Provenance"
Cohesion: 0.15
Nodes (17): AdDraft, AssetLedger, AssetLicense, MediaAsset, StrEnum, DisabledMediaRenderer, DryRunMediaRenderer, MediaRenderer (+9 more)

### Community 8 - "Unit Economics and Experiments"
Cohesion: 0.16
Nodes (22): AnalyticsEvent, CostLedger, ExperimentAssignment, Decimal, anomaly(), deterministic_assignment(), funnel(), Decimal (+14 more)

### Community 9 - "Trend Discovery Ranking"
Cohesion: 0.15
Nodes (13): TrendCandidate, DiscoveryPolicy, rank_candidates(), Rank signals only; this does not claim product-market fit or authorize listing., Protocol, Implement with an official or explicitly licensed data provider., StaticTrendSource, TrendSource (+5 more)

### Community 10 - "Supplier Fulfillment Boundary"
Cohesion: 0.23
Nodes (15): FulfillmentRequest, FulfillmentResult, Protocol, Supplier, SupplierOffer, validate_fulfillment_request(), validate_offer(), DryRunSupplier (+7 more)

### Community 11 - "HTTP App Assembly"
Cohesion: 0.18
Nodes (15): _authorize(), create_app(), Any, bearer_token(), Principal, configure_logging(), get_logger(), JsonFormatter (+7 more)

### Community 12 - "Operator Service and Shop Client"
Cohesion: 0.16
Nodes (9): OperatorService, Any, Any, Boundary for the official API; live mutation is opt-in and unimplemented in MVP., TikTokClient, service(), test_operator_decide_then_execute(), test_operator_health_and_pending_reviews() (+1 more)

### Community 13 - "Durable Review Persistence"
Cohesion: 0.20
Nodes (7): Any, Path, timedelta, Move abandoned EXECUTING items to FAILED so they are not stuck forever., Durable review state.      Every transition runs inside ``BEGIN IMMEDIATE`` and, SQLiteReviewStore, Connection

### Community 14 - "In-Process Review Queue"
Cohesion: 0.14
Nodes (7): Any, timedelta, Leave the item terminal. The outcome of a partial mutation is unknown,         s, In-process store for development and tests.      State does not survive a restar, ReviewQueue, client(), store()

### Community 15 - "API Endpoint Tests"
Cohesion: 0.18
Nodes (14): auth(), sign(), test_approvals_survive_a_process_restart(), test_conflicting_webhook_is_captured_for_review(), test_endpoints_fail_closed_when_no_tokens_are_configured(), test_execute_is_idempotent_and_does_not_replay_the_mutation(), test_malformed_webhook_returns_400(), test_out_of_order_webhook_is_not_silently_dropped() (+6 more)

### Community 16 - "Settings and Token Registry"
Cohesion: 0.21
Nodes (11): Bearer tokens resolved to named principals.      Only SHA-256 digests are config, Parse ``<sha256>:<name>:<role>[|<role>],...`` entries., TokenRegistry, _flag(), Boot without shop credentials so a dry-run deployment can start.          Creden, Settings, settings(), tokens() (+3 more)

### Community 17 - "Consent-Aware Retention"
Cohesion: 0.29
Nodes (11): RetentionEngine, ConsentStatus, CustomerProfile, LifecycleEvent, MessagePlan, StrEnum, event(), test_events_are_idempotent() (+3 more)

### Community 18 - "Launch Checklist Gate"
Cohesion: 0.25
Nodes (8): Derive what can be derived; take the rest as explicit attestation., LaunchChecklist, LaunchGate, Prevents live mutation until every operational control is attested., complete(), test_complete_explicit_gate_allows_next_layer(), test_explicit_enablement_is_required(), test_incomplete_checklist_blocks_live_mode()

### Community 19 - "Review Row Mapping"
Cohesion: 0.22
Nodes (11): _dt(), datetime, _row_to_item(), AlreadyExecuted, Exception, StrEnum, Raised when a claim loses to a call that already executed the item., Shared precondition so both stores refuse the same transitions. (+3 more)

### Community 20 - "Review Store Contract"
Cohesion: 0.22
Nodes (4): Protocol, Owns every review state transition so they stay atomic and durable., ReviewItem, ReviewStore

### Community 21 - "Review Durability Tests"
Cohesion: 0.25
Nodes (11): RecordingClient, seeded(), test_abandoned_execution_is_recovered_as_failed(), test_approval_survives_a_restart(), test_audit_trail_is_written_in_the_same_transaction(), test_both_stores_make_failure_terminal(), test_both_stores_reject_a_second_claim(), test_both_stores_share_the_decision_contract() (+3 more)

### Community 22 - "Fail-Closed Authentication"
Cohesion: 0.26
Nodes (11): AuthenticationNotConfigured, hash_token(), RuntimeError, Raised when no operator tokens exist; the API must fail closed, not open., Compare against every entry so timing does not reveal which one matched., registry(), test_multiple_roles_union_their_permissions(), test_only_digests_are_configured_so_env_leaks_are_not_credentials() (+3 more)

### Community 23 - "Disabled Browser Adapter"
Cohesion: 0.20
Nodes (8): BrowserAdapter, DisabledBrowserAdapter, Protocol, Default adapter: prevents accidental unattended browser activity., Boundary for approved, normal-session UI tasks when no API exists., test_browser_is_disabled_by_default(), test_review_cannot_be_decided_twice(), test_review_requires_explicit_decision()

### Community 24 - "Structured Logging"
Cohesion: 0.29
Nodes (5): get_correlation_id(), Any, Thin wrapper that forces every log call to be an event name plus safe fields., StructuredLogger, LogRecord

### Community 25 - "Launch Gate Boot Tests"
Cohesion: 0.50
Nodes (8): boot(), live(), test_dry_run_boots_without_any_attestation(), test_live_mode_boots_once_every_control_is_attested(), test_live_mode_refuses_to_boot_with_an_unattested_control(), test_live_mode_refuses_to_boot_without_operator_tokens(), test_live_mode_refuses_to_boot_without_shop_credentials(), test_live_mode_requires_explicit_enablement_beside_dry_run()

## Knowledge Gaps
- **8 isolated node(s):** `AgentSpec`, `ConversionEvent`, `tiktok-ops`, `check job (lint/format/types/tests)`, `In-memory AuditLog` (+3 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_logger()` connect `HTTP App Assembly` to `Order Webhook Ingestion`, `Model Client Failure Taxonomy`, `Approved Action Execution`, `Review Row Mapping`, `Structured Logging`?**
  _High betweenness centrality (0.098) - this node is a cross-community bridge._
- **Why does `ReviewItem` connect `Review Store Contract` to `Approved Action Execution`, `Agent Orchestration and Audit`, `Operator Service and Shop Client`, `Durable Review Persistence`, `In-Process Review Queue`, `API Endpoint Tests`, `Review Row Mapping`, `Review Durability Tests`, `Disabled Browser Adapter`?**
  _High betweenness centrality (0.058) - this node is a cross-community bridge._
- **Why does `ApprovedActionExecutor` connect `Approved Action Execution` to `Agent Orchestration and Audit`, `HTTP App Assembly`, `Operator Service and Shop Client`, `In-Process Review Queue`, `API Endpoint Tests`, `Review Row Mapping`, `Review Store Contract`, `Review Durability Tests`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `ReviewItem` (e.g. with `SQLiteReviewStore` and `ApprovedActionExecutor`) actually correct?**
  _`ReviewItem` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 15 inferred relationships involving `ApprovedActionExecutor` (e.g. with `Decision` and `OperatorService`) actually correct?**
  _`ApprovedActionExecutor` has 15 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `ReviewQueue` (e.g. with `Client` and `RecordingClient`) actually correct?**
  _`ReviewQueue` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `SQLiteReviewStore` (e.g. with `Decision` and `AuditEvent`) actually correct?**
  _`SQLiteReviewStore` has 6 INFERRED edges - model-reasoned connections that need verification._