# Graph Report - /Users/mirrr/ops-core  (2026-10-07)

## Corpus Check
- 166 files · ~65,189 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1624 nodes · 4251 edges · 99 communities (78 shown, 21 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 328 edges (avg confidence: 0.52)
- Token cost: 1,000 input · 500 output

## Community Hubs (Navigation)
- Observability Module
- Ai Module
- Test Module
- Publish Module
- Amazon Module
- Test Module
- Test Module
- Ai Module
- Test Module
- Growth Module
- Review Module
- Agents Module
- Api Module
- Test Module
- Db Module
- Publish Module
- Prove Module
- Test Module
- Media Module
- Analytics Module
- Test Module
- Domain Module
- Discovery Module
- Discovery Module
- Config Module
- Api Module
- Db Module
- Suppliers Module
- Test Module
- Discovery Module
- Domain Module
- Test Module
- Scan Module
- Amazon Module
- Test Module
- Test Module
- Amazon Module
- Test Module
- Publish Module
- Discovery Module
- Review Module
- Amazon Module
- Test Module
- Load Module
- Retention Module
- Agents Module
- Amazon Module
- Domain Module
- Test Module
- Amazon Module
- Api Module
- Ops Module
- Test Module
- Test Module
- Test Module
- Simulate Module
- Web Module
- Api Module
- Customer Module
- Db Module
- Domain Module
- Test Module
- Publish Module
- Workers Module
- Amazon Module
- Observability Module
- Amazon Module
- Test Module
- Test Module
- Ai Module
- Domain Module
- Review Module
- Publish Module
- Github Module
- Agents Module
- Ai Module
- Amazon Module
- Analytics Module
- Customer Module
- Db Module
- Discovery Module
- Domain Module
- Growth Module
- Init Module
- Media Module
- Ops Module
- Publish Module
- Retention Module
- Review Module
- Suppliers Module
- Web Module
- Web Module
- Compose Module
- Ops Module

## God Nodes (most connected - your core abstractions)
1. `ReviewItem` - 102 edges
2. `ApprovedActionExecutor` - 76 edges
3. `ReviewQueue` - 49 edges
4. `OrderStatus` - 43 edges
5. `RetryPolicy` - 43 edges
6. `SQLiteReviewStore` - 40 edges
7. `AuditLog` - 36 edges
8. `OrderEvent` - 35 edges
9. `SizeTier` - 33 edges
10. `create_app()` - 33 edges

## Surprising Connections (you probably didn't know these)
- `test_backoff_is_jittered()` --calls--> `RetryPolicy`  [INFERRED]
  tests/test_openai_responses.py → app/net.py
- `SimulatedEnvironment` --uses--> `CorvusWarden`  [INFERRED]
  scripts/simulate_contention_crucible.py → app/agents/corvus.py
- `WarehouseInventoryCatalog` --uses--> `CorvusWarden`  [INFERRED]
  scripts/simulate_contention_crucible.py → app/agents/corvus.py
- `Client` --uses--> `AgentRole`  [INFERRED]
  tests/test_agent_pipeline.py → app/agents/models.py
- `Client` --uses--> `TaskStatus`  [INFERRED]
  tests/test_agent_pipeline.py → app/agents/models.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Ops Core System Components** — docker_compose_services, app_web_dashboard_dashboard_ui, observability_prometheus_alerts_alerts [INFERRED 0.85]

## Communities (99 total, 21 thin omitted)

### Community 0 - "Observability Module"
Cohesion: 0.07
Nodes (26): AlertManager, AlertRule, default_rules(), Firing, Rising-edge alerts evaluated against the in-process metrics registry. No…, Fires each rule on the rising edge of its watched value. A rule whose value has…, Counter, execution_seconds() (+18 more)

### Community 1 - "Ai Module"
Cohesion: 0.09
Nodes (29): BookVisionProvider, GeminiBookVisionProvider, Any, Protocol, Protocol for book stack vision extraction providers., High-reliability client for Google Gemini Vision with structured schema output., CopyProviderError, RuntimeError (+21 more)

### Community 2 - "Test Module"
Cohesion: 0.10
Nodes (40): _matched_brand(), parse_bulk(), prefilter(), Decimal, QuickCandidate, Match a keyword as a whole word. Bare substring matching is unusable on product…, The fields you can read off a listing page or a bulk export without Keepa., First token or two of the title, which is where Amazon puts the brand. (+32 more)

### Community 3 - "Publish Module"
Cohesion: 0.08
Nodes (23): AbeBooksListingAdapter, AlibrisListingAdapter, AmazonSPAPIAdapter, BiblioListingAdapter, BookListingOrchestrator, EbayListingAdapter, Amazon Selling Partner API (SP-API) Adapter for Book Listings., Biblio Inventory API Adapter. (+15 more)

### Community 4 - "Amazon Module"
Cohesion: 0.09
Nodes (30): LandedCost, Duty is assessed on the customs value, not on freight or prep., What a unit costs to get into an Amazon fulfilment centre. Sourcing from China…, AmazonListing, One incumbent listing, as observed from a rank-history provider. Deliberately…, 1.0 is perfectly steady demand, 0.0 is wild. Coefficient of variation inverted.…, Reviews per point of rating headroom. A 3.9 with 4,000 reviews signals a market…, Many sellers on one ASIN means a reseller pile-on, not a private label. (+22 more)

### Community 5 - "Test Module"
Cohesion: 0.17
Nodes (39): A hard ceiling on model spend, enforced before each request. Token counts…, UsageLimit, http_error(), install(), payload(), provider(), raising(), A 400 is a request to fix, not one to resend. (+31 more)

### Community 6 - "Test Module"
Cohesion: 0.13
Nodes (33): RankPoint, codes(), landed(), listing(), ranks(), 4.8 stars means there is nothing for an improved product to fix., Without a supplier quote there is no honest margin, so no guess is made., The clearest argument for modelling Section 301 rather than ignoring it. Same… (+25 more)

### Community 7 - "Ai Module"
Cohesion: 0.16
Nodes (24): PolicyViolation, Generated copy failed the product policy gate and must not be used., copy_to_candidate(), CopyProvider, enforce_policy(), LocalCopyProvider, ProductCopy, ProductCopyRequest (+16 more)

### Community 8 - "Test Module"
Cohesion: 0.13
Nodes (27): break_even_price(), FeeSchedule, _money(), Decimal, Full per-unit economics at a given sale price. Refuses to run against an…, Lowest price that still clears `target_margin` after every fee. Solves for…, Amazon's cut of a sale, as data rather than constants. Every rate here is a…, unit_economics() (+19 more)

### Community 9 - "Growth Module"
Cohesion: 0.12
Nodes (21): campaign_url(), can_spend(), Decimal, rank_creatives(), Budget guardrail; callers must persist spend atomically., DisabledSocialChannel, Protocol, Prevents unattended publishing or ad spend until a reviewed adapter is… (+13 more)

### Community 10 - "Review Module"
Cohesion: 0.12
Nodes (14): Any, Protocol, timedelta, Leave the item terminal. The outcome of a partial mutation is unknown, so a…, Owns every review state transition so they stay atomic and durable., In-process store for development and tests. State does not survive a restart…, ReviewItem, ReviewQueue (+6 more)

### Community 11 - "Agents Module"
Cohesion: 0.17
Nodes (17): Register draft-only handlers; outputs become human-review items., register_publish_and_support_agents(), AgentRole, AgentSpec, AgentTask, Handoff, StrEnum, TaskStatus (+9 more)

### Community 12 - "Api Module"
Cohesion: 0.12
Nodes (20): _authorize(), create_app(), Any, AuthenticationNotConfigured, bearer_token(), Principal, RuntimeError, Raised when no operator tokens exist; the API must fail closed, not open. (+12 more)

### Community 13 - "Test Module"
Cohesion: 0.13
Nodes (22): PostgresOrderRepository, Any, Mark a conflicting event as handled. Returns False if it was not open., Durable order state with the same semantics as OrderStore., event(), NoDuplicateGuard, NoRowLock, Any (+14 more)

### Community 14 - "Db Module"
Cohesion: 0.14
Nodes (12): _dt(), Any, Connection, datetime, Path, timedelta, Move abandoned EXECUTING items to FAILED so they are not stuck forever. The…, Durable review state. Every transition runs inside ``BEGIN IMMEDIATE`` and… (+4 more)

### Community 15 - "Publish Module"
Cohesion: 0.16
Nodes (18): Decision, Reviewer identity is taken from the bearer token, never from the body., Mailer, Protocol, DryRunPublisher, Publisher, PublishGate, PublishRequest (+10 more)

### Community 16 - "Prove Module"
Cohesion: 0.13
Nodes (17): _default_migrations_dir(), discover(), main(), migrate(), Migration, MigrationError, Path, RuntimeError (+9 more)

### Community 17 - "Test Module"
Cohesion: 0.11
Nodes (21): Any, Path, Mark a conflicting event as handled. Returns False if it was not open., Durable local repository with the same semantics as OrderStore. Every event is…, SQLiteOrderRepository, fixture, repository(), event() (+13 more)

### Community 18 - "Media Module"
Cohesion: 0.15
Nodes (17): AdDraft, AssetLedger, AssetLicense, MediaAsset, StrEnum, DisabledMediaRenderer, DryRunMediaRenderer, MediaRenderer (+9 more)

### Community 19 - "Analytics Module"
Cohesion: 0.16
Nodes (22): AnalyticsEvent, CostLedger, ExperimentAssignment, Decimal, anomaly(), deterministic_assignment(), funnel(), Decimal (+14 more)

### Community 20 - "Test Module"
Cohesion: 0.13
Nodes (19): Shared precondition so both stores refuse the same transitions., _validate_claim(), CountingClient, Any, parametrize, race(), RaceResult, Prove at-most-once execution under contention, and prove the proof. The first… (+11 more)

### Community 21 - "Domain Module"
Cohesion: 0.20
Nodes (14): Postgres-backed order repository for multi-process deployments. The SQLite…, Return order IDs whose local status differs from the remote snapshot., reconcile(), evaluate_sla(), Order, OrderStatus, StrEnum, SLAEvaluation (+6 more)

### Community 22 - "Discovery Module"
Cohesion: 0.15
Nodes (13): TrendCandidate, DiscoveryPolicy, rank_candidates(), Rank signals only; this does not claim product-market fit or authorize listing., Protocol, Implement with an official or explicitly licensed data provider., StaticTrendSource, TrendSource (+5 more)

### Community 23 - "Discovery Module"
Cohesion: 0.12
Nodes (16): AbeBooksCompsProvider, CompsProvider, Decimal, Protocol, Round to friendly bookshop retail endings (.00, .50, or .95)., Protocol for market comps providers., Fast comps extractor querying AbeBooks and parsing Next.js streaming results., round_to_retail_ending() (+8 more)

### Community 24 - "Config Module"
Cohesion: 0.15
Nodes (17): Bearer tokens resolved to named principals. Only SHA-256 digests are…, Parse ``<sha256>:<name>:<role>[|<role>],...`` entries., TokenRegistry, _flag(), Boot without shop credentials so a dry-run deployment can start. Credentials…, Parse a boolean environment variable, failing closed on anything unclear. Every…, Settings, postgres_dsn() (+9 more)

### Community 25 - "Api Module"
Cohesion: 0.17
Nodes (14): handle_order_webhook(), OrderRepository, Any, Protocol, HTTP status is carried beside the body, never inside it., Framework-neutral webhook handler; never logs or returns raw payloads., WebhookResult, Extract only event/order/status identifiers from a webhook payload. (+6 more)

### Community 26 - "Db Module"
Cohesion: 0.17
Nodes (9): _json_safe(), PostgresReviewStore, Any, Connection, timedelta, Move abandoned EXECUTING items to FAILED so they are not stuck forever. The…, Match the SQLite store, which serializes results with ``default=str``., Durable review state shared by any number of worker processes. Every transition… (+1 more)

### Community 27 - "Suppliers Module"
Cohesion: 0.23
Nodes (15): FulfillmentRequest, FulfillmentResult, Protocol, Supplier, SupplierOffer, validate_fulfillment_request(), validate_offer(), DryRunSupplier (+7 more)

### Community 28 - "Test Module"
Cohesion: 0.15
Nodes (16): auth(), test_action_previews_require_auth_and_are_not_cacheable(), test_preview_decide_execute_result_flow(), test_session_permissions_and_blocked_execution(), sign(), test_conflicting_webhook_is_captured_for_review(), test_execute_is_idempotent_and_does_not_replay_the_mutation(), test_malformed_webhook_returns_400() (+8 more)

### Community 29 - "Discovery Module"
Cohesion: 0.16
Nodes (18): clean_isbn(), compute_string_similarity(), extract_potential_isbn(), is_valid_isbn_format(), Strip hyphens, spaces, and normalize to uppercase., Basic length check for ISBN-10 or ISBN-13., Extract first valid-looking ISBN from raw spine text., Extract lowercase alphanumeric tokens. (+10 more)

### Community 30 - "Domain Module"
Cohesion: 0.16
Nodes (19): PricingModel, ProductDraftBuilder, Decimal, Listed price that leaves ``margin`` of net revenue after costs and fees.…, Return changed quantities; caller decides whether to submit them., Everything between the listed price and what actually lands in the bank.…, Fraction of the listed price that never reaches contribution margin., sync_inventory() (+11 more)

### Community 31 - "Test Module"
Cohesion: 0.16
Nodes (20): OrderContentionManager, Thread-safe coordinator for cross-channel inventory reservations and contention…, make_claim(), If an order is placed well earlier than another, timestamp rules over channel…, When a duplicate copy exists in catalog, both orders are fulfilled., Corvus swoops in and auto-resolves standard marketplace contention., For rare/expensive books (> $200 CAD), Corvus leaves the conflict for the human…, 8 threads attempting to claim the same 1-of-1 SKU simultaneously. (+12 more)

### Community 32 - "Scan Module"
Cohesion: 0.17
Nodes (17): Finds Gemini API key from explicit arg, env, or local ~/.hermes/.env fallback., resolve_gemini_api_key(), BookStackExtraction, BookValuation, Full extraction result from an image of a pile or shelf of books., Pricing recommendation and channel strategy for Ottawa used bookstore intake., crop_spines(), get_spine_crop_base64() (+9 more)

### Community 33 - "Amazon Module"
Cohesion: 0.15
Nodes (13): StrEnum, Amazon US FBA size tiers, coarsest useful granularity., Net profit as a fraction of sale price. Negative means selling at a loss., Return on the cash actually tied up in inventory — the number that decides how…, SizeTier, UnitEconomics, FixedCosts, Costs paid once per brand, before a single unit sells. Every figure is a… (+5 more)

### Community 34 - "Test Module"
Cohesion: 0.15
Nodes (16): AlreadyExecuted, Exception, Raised when a claim loses to a call that already executed the item., fixture, RecordingClient, seeded(), store(), test_abandoned_execution_is_recovered_as_failed() (+8 more)

### Community 35 - "Test Module"
Cohesion: 0.15
Nodes (20): client(), _fake_urlopen(), _raise(), A request that errors was still served. Not counting it overspends the…, The scraper returned null price, empty buybox and empty sales_rank for a live…, Partial pages are usually a bot check or a slow render, so the next attempt…, Sixty candidates for one request is the whole point of searching first., The response ranks the product #10 in 'Garlic Keepers' and #35,786 in 'Kitchen… (+12 more)

### Community 36 - "Amazon Module"
Cohesion: 0.16
Nodes (8): LaunchPlan, _money(), Decimal, Profit on the first order, after the one-off brand costs are absorbed., Units that must sell before the launch has returned its own cost., Days between paying the supplier and the last unit turning into cash. The…, A first order for one product, priced end to end., Everything that leaves the bank before revenue arrives.

### Community 37 - "Test Module"
Cohesion: 0.17
Nodes (14): Any, Captures signed requests without contacting TikTok., SandboxTransport, Build a deterministic HMAC-SHA256 signature for request testing. The exact…, sign_request(), approved_store(), RecordingClient, test_executor_never_runs_the_mutation_twice() (+6 more)

### Community 38 - "Publish Module"
Cohesion: 0.13
Nodes (11): ContentionArbiter, ContentionResolution, Any, Protocol, Returns (winner, loser, reason)., Processes an incoming sale claim for a SKU across channels., Pushes a contention resolution action into the Operator Review Desk., The outcome of evaluating an order claim against current inventory allocations. (+3 more)

### Community 39 - "Discovery Module"
Cohesion: 0.16
Nodes (14): MetadataResolver, OpenLibraryMetadataResolver, Any, Protocol, Protocol for bibliographic catalog resolvers., Zero-key bibliographic resolver querying Open Library Search API., Evaluates whether this book requires opening to copyright page., infer_condition_grade() (+6 more)

### Community 40 - "Review Module"
Cohesion: 0.15
Nodes (9): generate_packing_slip(), Any, Generates a PDF packing slip and returns it as a base64 string., ApprovedActionExecutor, Any, Refuse actions whose adapter is switched off, before claiming the item. Nothing…, Only executes a narrow allowlist of approved operations, and only once. The…, client() (+1 more)

### Community 41 - "Amazon Module"
Cohesion: 0.15
Nodes (11): AmazonScraperClient, Path, Durable snapshot history and request accounting. The request log is the budget:…, Boundary for the scraper API, with the monthly allowance enforced. Dry-run by…, SnapshotStore, main(), Reasons this row is not worth a product request. Empty means look closer., triage() (+3 more)

### Community 42 - "Test Module"
Cohesion: 0.29
Nodes (15): DryRunMailer, Client, pipeline(), The agent → review → execute path, end to end. Every operation the agent layer…, Approving a publish review is necessary, not sufficient., run(), test_approval_alone_does_not_publish(), test_customer_response_reaches_the_mailer_only_after_approval() (+7 more)

### Community 43 - "Load Module"
Cohesion: 0.24
Nodes (14): Outcome, What the repository did with an event; mirrors the webhook response body., CountingClient, main(), open_backend(), open_order_backend(), percentiles(), phase_alert() (+6 more)

### Community 44 - "Retention Module"
Cohesion: 0.29
Nodes (11): RetentionEngine, ConsentStatus, CustomerProfile, LifecycleEvent, MessagePlan, StrEnum, event(), test_events_are_idempotent() (+3 more)

### Community 45 - "Agents Module"
Cohesion: 0.23
Nodes (7): CorvusWarden, Operator, Any, Protocol, Corvus, the Vault Warden. A sharp, algorithmic entity dwelling in the rafters…, Corvus inspects the pending queue. Returns a summary of his actions., Corvus's internal ruleset.

### Community 46 - "Amazon Module"
Cohesion: 0.23
Nodes (15): _category_rank(), _decimal(), _negative_share(), Any, Take the broadest ranked category, not the narrowest. A response ranks the…, Fraction of reviews at one or two stars. A better gap signal than the star…, Build a screenable listing from a snapshot plus accumulated history., to_listing() (+7 more)

### Community 47 - "Domain Module"
Cohesion: 0.21
Nodes (14): classify(), EventResolution, path_between(), StrEnum, How an observed platform event relates to the status we already hold., Shortest forward path through the lifecycle graph, or None if unreachable., Decide how to fold an out-of-order platform event into local state. Webhook…, Resolution (+6 more)

### Community 48 - "Test Module"
Cohesion: 0.22
Nodes (15): boot(), clean_env(), live(), fixture, parametrize, A typo must not be silently read as an attestation in either direction., Blanking a variable is the ordinary way to unset it. It used to read as True,…, test_an_unparseable_flag_is_a_deployment_fault() (+7 more)

### Community 49 - "Amazon Module"
Cohesion: 0.17
Nodes (11): IncompleteResponse, RuntimeError, Base for scraper failures., Retryable — timeout, network error, rate limit or 5xx., Not retryable — bad request, bad key, unusable response., The page came back partial — a null price, an empty buybox, no rank. Retryable…, ScraperError, TerminalScraperError (+3 more)

### Community 50 - "Api Module"
Cohesion: 0.23
Nodes (7): Any, Boundary for the official API; live mutation is opt-in and unimplemented in MVP., TikTokClient, service(), test_operator_decide_then_execute(), test_operator_health_and_pending_reviews(), test_operator_rejects_unknown_review()

### Community 51 - "Ops Module"
Cohesion: 0.25
Nodes (8): Derive what can be derived; take the rest as explicit attestation., LaunchChecklist, LaunchGate, Prevents live mutation until every operational control is attested., complete(), test_complete_explicit_gate_allows_next_layer(), test_explicit_enablement_is_required(), test_incomplete_checklist_blocks_live_mode()

### Community 52 - "Test Module"
Cohesion: 0.25
Nodes (10): OrderStore, Any, Small in-memory store for development; mirrors SQLiteOrderRepository semantics., event(), test_events_are_idempotent(), test_forward_jump_fast_forwards_and_reports_skipped_states(), test_late_event_does_not_regress_the_order(), test_reconcile_returns_status_drift() (+2 more)

### Community 53 - "Test Module"
Cohesion: 0.21
Nodes (14): plan(), The finding that matters. A healthy 27% margin still leaves the first order at…, Nothing about the product changes — only how far the fixed costs spread., Production and freight, then selling through, then Amazon's payout delay., A partial unit does not sell, so break-even is a ceiling. Rounding to nearest…, test_a_launch_needs_units(), test_a_loss_making_unit_has_no_break_even(), test_a_typical_first_order_does_not_pay_back_the_brand_setup() (+6 more)

### Community 54 - "Test Module"
Cohesion: 0.35
Nodes (10): BookValuationEngine, Calculates in-store and online pricing for Ottawa bookstore inventory., DisambiguationResult, Disambiguation and domain triage resolution for an extracted book spine., _mock_comps(), StaticCompsProvider, test_valuation_engine_bargain_bin(), test_valuation_engine_collectible_flag() (+2 more)

### Community 55 - "Simulate Module"
Cohesion: 0.41
Nodes (13): ContentionOutcome, OrderClaim, StrEnum, Represents a claim on a physical book SKU from a sales channel., header(), main(), Simulated Multi-Marketplace Crucible: Cross-Channel Inventory Contention…, run_scenario_four() (+5 more)

### Community 56 - "Web Module"
Cohesion: 0.40
Nodes (13): api(), disconnect(), fields(), human(), items, labels, message(), mutate() (+5 more)

### Community 57 - "Api Module"
Cohesion: 0.22
Nodes (4): OperatorService, Any, Mock database of duplicate copies for rare book titles., WarehouseInventoryCatalog

### Community 58 - "Customer Module"
Cohesion: 0.22
Nodes (8): CustomerResponse, HimalayaMailer, Thin subprocess boundary; disabled unless explicitly enabled by the caller. The…, Process arguments are world-readable via `ps`; message bodies are not., test_himalaya_sends_the_body_over_stdin_not_argv(), test_himalaya_still_refuses_unapproved_or_disabled(), test_email_dry_run_requires_approval_and_blocks_credentials(), test_himalaya_is_disabled_by_default()

### Community 59 - "Db Module"
Cohesion: 0.24
Nodes (8): is_postgres_dsn(), open_review_store(), Choose storage backends from configuration. ``REVIEW_DB`` and ``ORDER_DB`` are…, Postgres-backed review store for multi-process deployments. The SQLite store…, _row_to_item(), StrEnum, ReviewStatus, seed()

### Community 60 - "Domain Module"
Cohesion: 0.21
Nodes (8): CarrierAdapter, Any, Protocol, Maps a standard operations payload to a Shippo Shipment and Transaction., ShippoAdapter, patch, test_shippo_adapter_dry_run(), test_shippo_adapter_live_call()

### Community 61 - "Test Module"
Cohesion: 0.27
Nodes (10): format_sla_alert(), Send an operational alert. On the public ntfy.sh server a topic is only as…, send_ntfy(), parametrize, Order IDs on a public ntfy topic are readable by anyone who guesses it., test_opting_in_is_explicit(), test_sla_alert_payload_carries_no_buyer_data(), test_token_is_sent_and_self_hosted_server_is_honoured() (+2 more)

### Community 62 - "Publish Module"
Cohesion: 0.26
Nodes (3): Any, Decimal, Publishes the book and returns the external listing ID, or None if failed.

### Community 63 - "Workers Module"
Cohesion: 0.29
Nodes (7): Job, JobRunner, datetime, Deterministic retry runner; replace the queue backend without changing job…, test_audit_is_structured_and_append_only(), test_failed_job_moves_to_dead_letter(), test_job_retries_then_succeeds()

### Community 64 - "Amazon Module"
Cohesion: 0.20
Nodes (6): Estimate the FBA tier from weight alone. Dimensions are frequently absent from…, Fetch, persist, and return one snapshot. Costs one request., The monthly request allowance is spent; no request was made., One observation of a listing at a point in time. The scraper returns a single…, RequestBudgetExceeded, Snapshot

### Community 65 - "Observability Module"
Cohesion: 0.29
Nodes (5): get_correlation_id(), Any, Thin wrapper that forces every log call to be an event name plus safe fields., StructuredLogger, LogRecord

### Community 66 - "Amazon Module"
Cohesion: 0.24
Nodes (9): _monthly_sales(), A row from a search page. Enough to triage 60 products for one request., Map a search page, skipping rows too incomplete to judge., One request, up to sixty candidates. The cheapest way to find things., SearchResult, to_search_results(), A paid placement says the seller has an ad budget, not that buyers want it., test_search_maps_rows_and_skips_unusable_ones() (+1 more)

### Community 67 - "Test Module"
Cohesion: 0.42
Nodes (8): handle_square_webhook(), Any, verify_square_signature(), sign_square(), test_handle_square_webhook_ignored_event(), test_handle_square_webhook_invalid_signature(), test_handle_square_webhook_valid_order(), test_verify_square_signature()

### Community 68 - "Test Module"
Cohesion: 0.36
Nodes (9): hash_token(), parametrize, registry(), test_bearer_parsing(), test_malformed_entries_fail_at_startup(), test_multiple_roles_union_their_permissions(), test_only_digests_are_configured_so_env_leaks_are_not_credentials(), test_roles_map_to_disjoint_permissions() (+1 more)

### Community 69 - "Ai Module"
Cohesion: 0.22
Nodes (7): OpenAIResponsesCopyProvider, Hardened client for the Responses API. Synchronous by design: it is called from…, _sanitize_fact(), parametrize, test_local_test_server_is_allowed(), test_requires_key(), test_unsafe_base_urls_are_refused()

### Community 70 - "Domain Module"
Cohesion: 0.28
Nodes (3): Any, test_book_format_enum_and_fallback(), test_book_spine_item_from_and_to_dict()

### Community 71 - "Review Module"
Cohesion: 0.22
Nodes (6): BrowserAdapter, DisabledBrowserAdapter, Protocol, Default adapter: prevents accidental unattended browser activity., Boundary for approved, normal-session UI tasks when no API exists., test_browser_is_disabled_by_default()

### Community 72 - "Publish Module"
Cohesion: 0.25
Nodes (5): BookListingAdapter, Protocol, Protocol for marketplace-specific book listing APIs., Removes the book from the marketplace to prevent double-selling., Cancels an unfulfillable order on the marketplace (e.g. lost contention).

## Knowledge Gaps
- **11 isolated node(s):** `AgentSpec`, `ConversionEvent`, `items`, `labels`, `ops-core` (+6 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **21 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `get_logger()` connect `Api Module` to `Observability Module`, `Ai Module`, `Observability Module`, `Test Module`, `Amazon Module`, `Publish Module`, `Amazon Module`, `Db Module`, `Prove Module`, `Test Module`, `Publish Module`, `Test Module`, `Domain Module`, `Discovery Module`, `Api Module`, `Db Module`, `Discovery Module`?**
  _High betweenness centrality (0.150) - this node is a cross-community bridge._
- **Why does `ReviewItem` connect `Review Module` to `Scan Module`, `Observability Module`, `Test Module`, `Test Module`, `Review Module`, `Test Module`, `Agents Module`, `Api Module`, `Load Module`, `Db Module`, `Publish Module`, `Prove Module`, `Api Module`, `Test Module`, `Api Module`, `Db Module`, `Db Module`, `Test Module`?**
  _High betweenness centrality (0.101) - this node is a cross-community bridge._
- **Why does `RetryPolicy` connect `Amazon Module` to `Amazon Module`, `Ai Module`, `Amazon Module`, `Publish Module`, `Test Module`, `Ai Module`, `Test Module`, `Discovery Module`, `Publish Module`, `Amazon Module`, `Api Module`, `Amazon Module`, `Test Module`, `Discovery Module`, `Discovery Module`?**
  _High betweenness centrality (0.092) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `ReviewItem` (e.g. with `OperatorService` and `PostgresReviewStore`) actually correct?**
  _`ReviewItem` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 25 inferred relationships involving `ApprovedActionExecutor` (e.g. with `Decision` and `OperatorService`) actually correct?**
  _`ApprovedActionExecutor` has 25 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `ReviewQueue` (e.g. with `SimulatedEnvironment` and `WarehouseInventoryCatalog`) actually correct?**
  _`ReviewQueue` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 14 inferred relationships involving `OrderStatus` (e.g. with `OrderRepository` and `WebhookResult`) actually correct?**
  _`OrderStatus` has 14 INFERRED edges - model-reasoned connections that need verification._