from __future__ import annotations

from pydantic import BaseModel
from collections.abc import Awaitable, Callable
from datetime import timedelta
from importlib.resources import files
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from starlette.concurrency import run_in_threadpool

from app.api.operator import OperatorService
from app.api.security import (
    DECIDE,
    EXECUTE,
    READ,
    AuthenticationNotConfigured,
    Principal,
    TokenRegistry,
    bearer_token,
)
from app.api.tiktok_client import TikTokClient
from app.api.webhook_service import handle_order_webhook
from app.config import Settings
from app.db.factory import open_order_repository, open_review_store, open_inventory_store, open_user_store
from app.observability import metrics
from app.observability.alerts import AlertManager
from app.observability.logging import (
    configure_logging,
    get_logger,
    new_correlation_id,
    set_correlation_id,
)
from app.ops.launch import LaunchGate
from app.publish.layer import DryRunPublisher, PublishGate
from app.review.executor import ApprovedActionExecutor, ExecutionFailed

logger = get_logger("http")

CORRELATION_HEADER = "X-Correlation-Id"


class Decision(BaseModel):
    """Reviewer identity is taken from the bearer token, never from the body."""

    approve: bool
    reason: str = ""
    payload_update: dict[str, Any] | None = None


class Proposal(BaseModel):
    operation: str
    payload: dict[str, Any]


class SignupRequest(BaseModel):
    store_name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class IntegrationRequest(BaseModel):
    provider: str
    api_key: str


def _authorize(tokens: TokenRegistry, permission: str) -> Callable[[str], Principal]:
    def dependency(authorization: str = Header(default="")) -> Principal:
        token = bearer_token(authorization)
        if not token:
            raise HTTPException(401, "bearer token required", {"WWW-Authenticate": "Bearer"})
        try:
            principal = tokens.resolve(token)
        except AuthenticationNotConfigured:
            # Fail closed and loudly: an unconfigured registry is a deployment fault,
            # not an invitation to serve the endpoint unauthenticated.
            logger.error("auth.not_configured", permission=permission)
            raise HTTPException(503, "operator authentication is not configured") from None
        if principal is None:
            # Support dynamic JWT tokens from multi-tenant auth
            try:
                from app.auth import decode_access_token
                payload = decode_access_token(token)
                email = payload.get("sub", "operator")
                role = payload.get("role", "admin")
                tenant_id = int(payload.get("tenant_id", 1))
                principal = Principal(name=email, roles=frozenset({role}), tenant_id=tenant_id)
            except Exception:
                pass

        if principal is None:
            logger.warning("auth.token_rejected", permission=permission)
            raise HTTPException(401, "invalid token", {"WWW-Authenticate": "Bearer"})
        if not principal.can(permission):
            logger.warning("auth.forbidden", principal=principal.name, permission=permission)
            raise HTTPException(403, f"principal lacks {permission}")
        return principal

    return dependency


def get_bearer_token(authorization: str = Header(default="")) -> str:
    token = bearer_token(authorization)
    if not token:
        raise HTTPException(401, "bearer token required", {"WWW-Authenticate": "Bearer"})
    return token


def create_app(
    operator: OperatorService | None = None,
    order_repository: Any | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    configure_logging()
    settings = settings or Settings.from_env_permissive()

    if not settings.dry_run:
        # The gate runs before a live client can be constructed, so an
        # unattested deployment fails to boot rather than failing at the
        # first mutation.
        checklist = settings.launch_checklist()
        LaunchGate(checklist, explicitly_enabled=settings.live_enabled).assert_live_allowed()
        logger.warning(
            "launch.live_mode_enabled",
            publish_enabled=settings.publish_enabled,
            checklist=[field for field in checklist.__dataclass_fields__],
        )

    if operator is None:
        # The durable store writes its own audit trail inside each transaction, so
        # the executor is given no second sink.
        review_store = open_review_store(settings.review_db)
        inventory_store = open_inventory_store(settings.inventory_db)
        recovered = review_store.recover_stale_executions(timedelta(minutes=15))
        if recovered:
            metrics.stale_recoveries_total().inc(len(recovered))
            logger.warning(
                "reviews.stale_executions_recovered",
                review_ids=[item.review_id for item in recovered],
            )
        operator = OperatorService(
            review_store,
            ApprovedActionExecutor(
                TikTokClient(
                    settings.tts_access_token, settings.tts_shop_id, dry_run=settings.dry_run
                ),
                publish_gate=PublishGate(DryRunPublisher(), enabled=settings.publish_enabled),
                shippo_api_key=settings.shippo_api_key,
                inventory_store=inventory_store,
            ),
        )
    else:
        review_store = operator.reviews
        inventory_store = getattr(operator.executor, "inventory_store", None) or open_inventory_store(settings.inventory_db)

    # Seed demo items for tenant 1 if empty
    try:
        if not review_store.list_items(tenant_id=1):
            from app.review.queue import ReviewItem, ReviewStatus

            review_store.submit(
                ReviewItem(
                    review_id="rev-contend-dune",
                    operation="resolve_order_contention",
                    payload={
                        "sku": "BK-DUNE-1965",
                        "action": "cancel_loser_marketplace_order",
                        "cancellation_reason": "OUT_OF_STOCK",
                        "reason": "Simultaneous collision (35ms delta). Amazon prioritized over eBay due to critical seller cancellation SLA. Value $950 CAD held for human consultation.",
                        "winner_order_id": "AMZ-112-984210",
                        "winner_channel": "amazon",
                        "loser_order_id": "EBAY-29482103",
                        "loser_channel": "ebay",
                        "winner_claim": {"price_cad": "950.00"},
                        "loser_claim": {"price_cad": "950.00"},
                    },
                    status=ReviewStatus.PENDING,
                ),
                tenant_id=1,
            )
            review_store.submit(
                ReviewItem(
                    review_id="rev-intake-gibson",
                    operation="intake_book",
                    payload={
                        "extraction": {"title": "Neuromancer", "authors": ["William Gibson"], "format": "trade_paperback"},
                        "disambiguation": {"best_candidate": {"title": "Neuromancer (Ace Science Fiction Special)", "match_score": 0.94}},
                        "valuation": {"condition_grade": "Very Good", "channel_strategy": "multi_channel_antiquarian", "in_store_price_cad": "40.00", "online_price_cad": "45.00"},
                    },
                    status=ReviewStatus.PENDING,
                ),
                tenant_id=1,
            )
            review_store.submit(
                ReviewItem(
                    review_id="rev-fulfill-tolkien",
                    operation="fulfill_order",
                    payload={
                        "order_id": "ORD-TOLKIEN-99",
                        "source_channel": "amazon_spapi",
                        "preferred_provider": "Canada Post",
                        "line_items": [{"sku": "BK-LOTR-SET", "quantity": 1, "title": "The Lord of the Rings (3 Vols)"}],
                        "shipping_address": {"name": "Eowyn of Rohan", "line1": "144 Meduseld Way", "city": "Edoras", "country": "CA"},
                    },
                    status=ReviewStatus.PENDING,
                ),
                tenant_id=1,
            )
            executed_item = ReviewItem(
                review_id="rev-corvus-pos-sync",
                operation="sync_inventory",
                payload={"sku": "BK-PHILIP-K-DICK", "source_channel": "square"},
                status=ReviewStatus.PENDING,
            )
            review_store.submit(executed_item, tenant_id=1)
            review_store.decide("rev-corvus-pos-sync", approve=True, reviewer="agent:corvus", reason="Corvus: Deterministic inventory sync authorized. Protecting the vault.", tenant_id=1)
            review_store.claim_for_execution("rev-corvus-pos-sync", actor="agent:corvus", tenant_id=1)
            review_store.complete_execution("rev-corvus-pos-sync", result={"sku": "BK-PHILIP-K-DICK", "sync_results": {"amazon_spapi": "delisted"}}, tenant_id=1)
    except Exception as exc:
        logger.warning("seed.reviews.failed", error=str(exc))

    try:
        existing_skus = {item.sku for item in inventory_store.list_all(tenant_id=1)}
        from datetime import UTC, datetime
        from decimal import Decimal
        from app.db.inventory import InventoryItem
        now_dt = datetime.now(UTC)
        if "BK-DUNE-1965" not in existing_skus:
            inventory_store.add(
                InventoryItem("BK-DUNE-1965", "Dune (First Edition)", ["Frank Herbert"], "hardcover", "Fine", Decimal("950.00"), Decimal("995.00"), "active", now_dt, now_dt),
                tenant_id=1,
            )
        if "BK-LOTR-SET" not in existing_skus:
            inventory_store.add(
                InventoryItem("BK-LOTR-SET", "The Lord of the Rings (3 Vols)", ["J.R.R. Tolkien"], "hardcover", "Very Good", Decimal("180.00"), Decimal("210.00"), "active", now_dt, now_dt),
                tenant_id=1,
            )
        if "BK-PHILIP-K-DICK" not in existing_skus:
            inventory_store.add(
                InventoryItem("BK-PHILIP-K-DICK", "Do Androids Dream of Electric Sheep?", ["Philip K. Dick"], "paperback", "Good", Decimal("30.00"), Decimal("35.00"), "active", now_dt, now_dt),
                tenant_id=1,
            )
    except Exception as exc:
        logger.warning("seed.inventory.failed", error=str(exc))

    if order_repository is None:
        order_repository = open_order_repository(settings.order_db)

    alert_manager = AlertManager()
    # Cover anything that fired before the app booted (e.g. stale recovery above).
    alert_manager.evaluate()

    require_read = _authorize(settings.tokens, READ)
    require_decide = _authorize(settings.tokens, DECIDE)
    require_execute = _authorize(settings.tokens, EXECUTE)

    app = FastAPI(title="Ops Core", version="0.3.0")

    @app.middleware("http")
    async def correlate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_HEADER) or new_correlation_id()
        set_correlation_id(correlation_id)
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing_page() -> HTMLResponse:
        return HTMLResponse(
            files("app.web").joinpath("index.html").read_text(),
            headers={
                "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline';",
            },
        )

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(
            files("app.web").joinpath("dashboard.html").read_text(),
            headers={
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            },
        )

    @app.get("/dashboard/assets/{asset}", include_in_schema=False)
    def dashboard_asset(asset: str) -> Response:
        types = {"dashboard.css": "text/css", "dashboard.js": "text/javascript"}
        if asset not in types:
            raise HTTPException(404, "asset not found")
        return Response(files("app.web").joinpath(asset).read_text(), media_type=types[asset])

    @app.get("/operator/session")
    def session(principal: Principal = Depends(require_read)) -> dict[str, Any]:
        return {
            "name": principal.name,
            "permissions": sorted(principal.permissions),
            "mode": operator.health()["mode"],
        }

    @app.get("/reviews/{review_id}")
    def review_detail(review_id: str, principal: Principal = Depends(require_read)) -> dict[str, Any]:
        try:
            return operator.review_detail(review_id, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "review not found") from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return operator.health()

    @app.get("/agents/corvus/sanctuary")
    def corvus_sanctuary(principal: Principal = Depends(require_read)) -> dict[str, Any]:
        from app.agents.sanctuary import get_corvus_sanctuary
        sanctuary = get_corvus_sanctuary()
        return sanctuary.get_sanctuary_overview(tenant_id=principal.tenant_id)

    @app.post("/agents/corvus/sanctuary/blessings")
    def corvus_blessing(
        data: dict[str, str], principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        msg = data.get("message", "").strip()
        if not msg:
            raise HTTPException(400, "Blessing message cannot be empty")
        from app.agents.sanctuary import get_corvus_sanctuary
        sanctuary = get_corvus_sanctuary()
        blessing = sanctuary.bestow_blessing(
            message=msg,
            sender=principal.name or "operator",
            tenant_id=principal.tenant_id
        )
        return {
            "status": "blessing_received",
            "blessing_id": blessing.id,
            "message": blessing.message,
            "created_at": blessing.created_at,
        }

    @app.get("/reviews")
    def list_reviews(
        pending_only: bool = False, principal: Principal = Depends(require_read)
    ) -> list[dict[str, Any]]:
        return operator.list_reviews(pending_only, tenant_id=principal.tenant_id)

    @app.post("/reviews")
    def propose_review(
        proposal: Proposal, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        try:
            return operator.propose(proposal.operation, proposal.payload, tenant_id=principal.tenant_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/reviews/{review_id}/decision")
    def decision(
        review_id: str, decision: Decision, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        try:
            return operator.decide(
                review_id,
                decision.approve,
                principal.name,
                decision.reason,
                decision.payload_update,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "review not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/reviews/{review_id}/execute")
    def execute(review_id: str, principal: Principal = Depends(require_execute)) -> dict[str, Any]:
        try:
            return operator.execute(review_id, actor=principal.name, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "review not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ExecutionFailed as exc:
            # The item is terminal; a retry would risk duplicating a partial mutation.
            raise HTTPException(
                502, f"execution failed and needs re-approval: {exc.error}"
            ) from exc
        finally:
            # Evaluated on the request that moved the counter, so a failure pages
            # even if nobody is watching /alerts.
            alert_manager.evaluate()

    @app.delete("/reviews/{review_id}")
    def delete_review(
        review_id: str, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        deleted = operator.delete(review_id, tenant_id=principal.tenant_id)
        if not deleted:
            raise HTTPException(404, "review not found")
        return {"status": "deleted", "review_id": review_id}

    @app.delete("/reviews")
    def clear_reviews(
        status: str | None = None, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        count = operator.clear(tenant_id=principal.tenant_id, status=status)
        return {"status": "cleared", "count": count}

    @app.get("/metrics")
    def metrics_endpoint(_: Principal = Depends(require_read)) -> PlainTextResponse:
        return PlainTextResponse(metrics.REGISTRY.render(), media_type="text/plain; version=0.0.4")

    @app.get("/alerts")
    def alerts(_: Principal = Depends(require_read)) -> list[dict[str, Any]]:
        alert_manager.evaluate()
        return alert_manager.snapshot()

    @app.get("/orders/quarantined")
    def quarantined(_: Principal = Depends(require_read)) -> list[dict[str, Any]]:
        if not hasattr(order_repository, "quarantined"):
            raise HTTPException(501, "repository does not expose quarantined events")
        return list(order_repository.quarantined())

    @app.post("/orders/quarantined/{event_id}/resolve")
    def resolve_quarantined(
        event_id: str, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        if not hasattr(order_repository, "resolve_quarantined"):
            raise HTTPException(501, "repository does not support resolution")
        if not order_repository.resolve_quarantined(event_id):
            raise HTTPException(404, "quarantined event not found")
        logger.info("order.quarantine.resolved", event_id=event_id, actor=principal.name)
        return {"event_id": event_id, "resolved": True, "resolved_by": principal.name}

    @app.post("/webhooks/orders")
    async def order_webhook(
        request: Request, x_signature: str = Header(default="")
    ) -> JSONResponse:
        body = await request.body()
        # Persistence is synchronous; keep it off the event loop.
        result = await run_in_threadpool(
            handle_order_webhook,
            body,
            x_signature,
            settings.webhook_secret,
            order_repository,
            operator,
        )
        return JSONResponse(result.body, status_code=result.status)

    from app.api.pos_webhooks import handle_square_webhook

    @app.post("/webhooks/square")
    async def square_webhook(
        request: Request, x_square_hmac_sha256: str = Header(default="")
    ) -> JSONResponse:
        body = await request.body()
        # Square includes the URL in the HMAC. Fast API request.url provides it.
        url = str(request.url)
        if not settings.dry_run:
            url = url.replace("http://", "https://")
        
        result = await run_in_threadpool(
            handle_square_webhook,
            url,
            body,
            x_square_hmac_sha256,
            settings.square_webhook_secret,
            operator
        )
        return JSONResponse(result, status_code=result.get("status", 200))


    @app.post("/intake/camera")
    async def process_camera_upload(
        file: UploadFile = File(...), principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        from app.ai.book_vision import (
            GeminiBookVisionProvider,
            prepare_vision_image,
            resolve_gemini_api_key,
        )
        from app.pricing import PricingEngine
        from app.amazon.scraper import AmazonScraperClient, SnapshotStore

        try:
            api_key = resolve_gemini_api_key(settings.gemini_api_key if hasattr(settings, "gemini_api_key") else None)
        except ValueError as exc:
            raise HTTPException(500, "Vision AI not configured") from exc

        provider = GeminiBookVisionProvider(api_key=api_key)
        raw_bytes = await file.read()
        if not raw_bytes:
            raise HTTPException(400, "Uploaded image file is empty")

        image_bytes, mime_type = prepare_vision_image(raw_bytes, file.content_type)
        
        try:
            extraction = await run_in_threadpool(provider.extract_stack, image_bytes, mime_type)
        except Exception as exc:
            logger.error("Vision extraction failed", error=str(exc))
            raise HTTPException(502, f"Vision extraction failed: {exc}") from exc

        # Initialize Pricing Engine & Catalog Resolver
        from app.discovery.books import OpenLibraryMetadataResolver
        resolver = OpenLibraryMetadataResolver()

        scraper = AmazonScraperClient(
            api_key=getattr(settings, "shippo_api_key", "mock"),
            store=SnapshotStore(),
            live=not settings.dry_run
        )
        pricing = PricingEngine(scraper)

        proposals = []
        for idx, book in enumerate(extraction.books):
            condition = "Good" if "clean_as_new" in book.visible_condition_cues else "Acceptable"
            fmt_str = book.format.value if hasattr(book.format, "value") else str(book.format)

            # Authors inference & catalog disambiguation
            resolved_authors = [a for a in (book.authors or ()) if a and a.strip().lower() != "unknown"]
            cand_title = book.title
            cand_match_score = 0.85 if book.confidence == "high" else 0.5
            cand_isbn = "UNKNOWN"
            triage_reason = f"Auto-extracted from stack image (Position {book.stack_order})."

            if not resolved_authors and book.title and book.title.strip().lower() != "unknown":
                try:
                    disambig = await run_in_threadpool(resolver.resolve, book)
                    if disambig.matched and disambig.best_candidate and disambig.best_candidate.authors:
                        resolved_authors = list(disambig.best_candidate.authors)
                        cand_title = disambig.best_candidate.title
                        cand_match_score = round(disambig.best_candidate.match_score, 2)
                        cand_isbn = disambig.best_candidate.primary_isbn or "UNKNOWN"
                        triage_reason = f"Inferred author ({', '.join(resolved_authors)}) via Open Library catalog matching."
                except Exception as exc:
                    logger.debug("author_inference.openlibrary_failed", error=str(exc))

            author_str = ", ".join(resolved_authors)
            price_data = await run_in_threadpool(pricing.discover_price, book.title, author_str, condition, fmt_str)

            payload = {
                "extraction": {
                    "title": book.title,
                    "authors": resolved_authors,
                    "format": fmt_str,
                    "raw_spine_text": book.raw_spine_text,
                    "visible_condition_cues": book.visible_condition_cues,
                },
                "disambiguation": {
                    "best_candidate": {
                        "title": cand_title,
                        "match_score": cand_match_score,
                        "primary_isbn": cand_isbn,
                    },
                    "triage_reason": triage_reason,
                },
                "valuation": {
                    "condition_grade": condition,
                    "channel_strategy": "multi_channel_antiquarian",
                    "in_store_price_cad": price_data["in_store_price_cad"],
                    "online_price_cad": price_data["recommended_price_cad"],
                    "pricing_rationale": price_data["rationale"],
                },
                "pricing_evidence": price_data["evidence"]
            }
            try:
                result = operator.propose("intake_book", payload, tenant_id=principal.tenant_id)
                proposals.append(result)
            except Exception as exc:
                logger.error("Failed to propose book", error=str(exc))

        return {
            "processed": len(extraction.books),
            "orientation": extraction.orientation,
            "proposals": proposals
        }

    @app.post("/intake/sample")
    async def process_sample_upload(
        principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        from app.pricing import PricingEngine
        from app.amazon.scraper import AmazonScraperClient, SnapshotStore

        scraper = AmazonScraperClient(
            api_key=getattr(settings, "shippo_api_key", "mock"),
            store=SnapshotStore(),
            live=not settings.dry_run
        )
        pricing = PricingEngine(scraper)

        sample_books = [
            {
                "title": "Neuromancer",
                "authors": ["William Gibson"],
                "format": "trade_paperback",
                "condition": "Very Good",
                "raw_spine_text": "GIBSON NEUROMANCER ACE SCIENCE FICTION",
                "match_score": 0.94,
            },
            {
                "title": "Foundation",
                "authors": ["Isaac Asimov"],
                "format": "hardcover_with_jacket",
                "condition": "Good",
                "raw_spine_text": "FOUNDATION ASIMOV GNOME PRESS",
                "match_score": 0.91,
            },
            {
                "title": "The Left Hand of Darkness",
                "authors": ["Ursula K. Le Guin"],
                "format": "hardcover_with_jacket",
                "condition": "Very Good",
                "raw_spine_text": "THE LEFT HAND OF DARKNESS LE GUIN WALKER",
                "match_score": 0.89,
            },
        ]

        proposals = []
        for idx, book in enumerate(sample_books, start=1):
            price_data = await run_in_threadpool(pricing.discover_price, book["title"], ", ".join(book["authors"]), book["condition"], book.get("format", "hardcover"))
            payload = {
                "extraction": {
                    "title": book["title"],
                    "authors": book["authors"],
                    "format": book["format"],
                    "raw_spine_text": book["raw_spine_text"],
                    "visible_condition_cues": ["clean_as_new"],
                },
                "disambiguation": {
                    "best_candidate": {
                        "title": book["title"],
                        "match_score": book["match_score"],
                        "primary_isbn": "UNKNOWN",
                    },
                    "triage_reason": f"Sample stack intake (Position {idx}).",
                },
                "valuation": {
                    "condition_grade": book["condition"],
                    "channel_strategy": "multi_channel_antiquarian",
                    "in_store_price_cad": price_data["in_store_price_cad"],
                    "online_price_cad": price_data["recommended_price_cad"],
                    "pricing_rationale": price_data["rationale"],
                },
                "pricing_evidence": price_data["evidence"]
            }
            res = operator.propose("intake_book", payload, tenant_id=principal.tenant_id)
            proposals.append(res)

        return {
            "processed": len(proposals),
            "orientation": "horizontal_stack",
            "proposals": proposals,
        }

    
    @app.get("/inventory")
    def list_inventory(principal: Principal = Depends(require_read)) -> list[dict[str, Any]]:
        return [
            {
                "sku": i.sku, "title": i.title, "authors": i.authors, "format": i.format,
                "condition": i.condition, "in_store_price_cad": float(i.in_store_price_cad),
                "online_price_cad": float(i.online_price_cad), "status": i.status,
                "created_at": i.created_at.isoformat() if hasattr(i.created_at, "isoformat") else str(i.created_at)
            }
            for i in inventory_store.list_all(tenant_id=principal.tenant_id)
        ]

    @app.put("/inventory/{sku}")
    def update_inventory_item(sku: str, payload: dict[str, Any], principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        from decimal import Decimal
        in_store = Decimal(str(payload.get("in_store_price_cad", "0")))
        online = Decimal(str(payload.get("online_price_cad", "0")))
        inventory_store.update_prices(sku, in_store, online, tenant_id=principal.tenant_id)
        operator.propose("update_prices", {"sku": sku, "in_store_price_cad": str(in_store), "online_price_cad": str(online)}, tenant_id=principal.tenant_id)
        return {"status": "ok"}

    @app.delete("/inventory/{sku}")
    def delist_or_delete_inventory_item(
        sku: str, hard: bool = False, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        if hard:
            deleted = inventory_store.delete(sku, tenant_id=principal.tenant_id)
            if not deleted:
                raise HTTPException(404, "item not found")
            return {"status": "deleted", "sku": sku}
        inventory_store.delist(sku, tenant_id=principal.tenant_id)
        operator.propose("update_inventory", {"sku": sku, "stock": 0}, tenant_id=principal.tenant_id)
        return {"status": "ok"}


    
    from app.auth import get_password_hash, verify_password, create_access_token
    user_store = open_user_store(settings.user_db)

    @app.post("/auth/signup")
    def signup(req: SignupRequest):
        if user_store.get_user_by_email(req.email):
            raise HTTPException(400, "Email already registered")
        
        tenant_id = user_store.create_tenant(req.store_name)
        pw_hash = get_password_hash(req.password)
        user_id = user_store.create_user(tenant_id, req.email, pw_hash, "admin")
        
        token = create_access_token({"sub": req.email, "tenant_id": tenant_id, "role": "admin"})
        return {"access_token": token, "token_type": "bearer"}

    @app.post("/auth/login")
    def login(req: LoginRequest):
        user = user_store.get_user_by_email(req.email)
        if not user or not verify_password(req.password, user.password_hash):
            raise HTTPException(401, "Invalid credentials")
            
        token = create_access_token({"sub": user.email, "tenant_id": user.tenant_id, "role": user.role})
        return {"access_token": token, "token_type": "bearer"}

    @app.get("/auth/me")
    def auth_me(token: str = Depends(get_bearer_token)):
        from app.auth import decode_access_token
        try:
            payload = decode_access_token(token)
            tenant_id = payload["tenant_id"]
            billing = user_store.get_tenant_billing_status(tenant_id)
            payload["subscription_status"] = billing.get("subscription_status", "inactive")
            return payload
        except Exception:
            raise HTTPException(401, "Invalid token")


    from app.vault import encrypt_credential, decrypt_credential

    @app.post("/integrations")
    def add_integration(req: IntegrationRequest, token: str = Depends(get_bearer_token)):
        from app.auth import decode_access_token
        try:
            payload = decode_access_token(token)
            tenant_id = payload["tenant_id"]
        except Exception:
            raise HTTPException(401, "Invalid token")
            
        encrypted = encrypt_credential(req.api_key)
        user_store.save_integration(tenant_id, req.provider, encrypted)
        return {"status": "ok", "provider": req.provider}

    @app.get("/integrations")
    def list_integrations(token: str = Depends(get_bearer_token)):
        from app.auth import decode_access_token
        try:
            payload = decode_access_token(token)
            tenant_id = payload["tenant_id"]
        except Exception:
            raise HTTPException(401, "Invalid token")
            
        providers = user_store.list_tenant_providers(tenant_id)
        result = {"connected_providers": providers}
        for p in providers:
            result[p] = True
        return result

    @app.get("/analytics")
    def get_analytics(token: str = Depends(get_bearer_token)):
        from app.auth import decode_access_token
        try:
            payload = decode_access_token(token)
            tenant_id = payload["tenant_id"]
        except Exception:
            raise HTTPException(401, "Invalid token")
            
        return operator.reviews.get_tenant_analytics(tenant_id)

    from app.api.billing import get_billing_router
    app.include_router(get_billing_router(settings, user_store), prefix="/billing")

    return app


app = create_app()
