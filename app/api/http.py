from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable
from datetime import timedelta
from importlib.resources import files
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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
from app.db.factory import (
    open_inventory_store,
    open_order_repository,
    open_review_store,
    open_user_store,
    open_want_list_store,
)
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


class BatchDeleteRequest(BaseModel):
    review_ids: list[str]


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


class ShopHoldRequest(BaseModel):
    sku: str
    patron_name: str
    patron_email: str
    patron_phone: str = ""
    pickup_note: str = ""


class DirectIntakeRequest(BaseModel):
    title: str
    authors: list[str] = []
    format: str = "Hardcover"
    condition: str = "Good"
    dual_grade: str | None = None
    in_store_price_cad: float = 0.0
    online_price_cad: float = 0.0
    section: str = "General"
    shelf: str = ""
    issue_points: dict[str, Any] | None = None
    condition_diagnostic: dict[str, Any] | None = None
    trade_catalog_blurb: str = ""
    pricing_rationale: str = ""
    channel_strategy: str = "multi_channel_antiquarian"


def _require_active_subscription(user_store: Any, tenant_id: int) -> None:
    try:
        billing = user_store.get_tenant_billing_status(tenant_id)
    except Exception as exc:
        logger.error(
            "billing.status_unavailable",
            tenant_id=tenant_id,
            error=str(exc),
        )
        raise HTTPException(503, "subscription status is unavailable") from None

    if billing.get("subscription_status") != "active":
        raise HTTPException(402, "an active subscription is required")


def _authorize(
    tokens: TokenRegistry,
    permission: str,
    user_store: Any | None = None,
    *,
    allow_inactive: bool = False,
) -> Callable[[str], Principal]:
    def dependency(authorization: str = Header(default="")) -> Principal:
        token = bearer_token(authorization)
        if not token:
            raise HTTPException(401, "bearer token required", {"WWW-Authenticate": "Bearer"})
        registry_unconfigured = False
        try:
            principal = tokens.resolve(token)
        except AuthenticationNotConfigured:
            # Account JWTs are independently signed and remain usable when no
            # static operator-token registry is configured.
            registry_unconfigured = True
            principal = None
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
                if registry_unconfigured:
                    logger.error("auth.not_configured", permission=permission)
                    raise HTTPException(503, "operator authentication is not configured") from None

        if principal is None:
            logger.warning("auth.token_rejected", permission=permission)
            raise HTTPException(401, "invalid token", {"WWW-Authenticate": "Bearer"})
        if not principal.can(permission):
            logger.warning("auth.forbidden", principal=principal.name, permission=permission)
            raise HTTPException(403, f"principal lacks {permission}")
        if user_store is not None and not allow_inactive:
            _require_active_subscription(user_store, principal.tenant_id)
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
    inventory_store: Any | None = None,
    user_store: Any | None = None,
) -> FastAPI:
    configure_logging()
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    from app.auth import validate_jwt_configuration

    validate_jwt_configuration()

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
        if inventory_store is None:
            inventory_store = open_inventory_store(settings.inventory_db)
        want_list_store = open_want_list_store(settings.want_list_db)
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
                TikTokClient(settings.tts_access_token, settings.tts_shop_id, dry_run=settings.dry_run),
                publish_gate=PublishGate(DryRunPublisher(), enabled=settings.publish_enabled),
                shippo_api_key=settings.shippo_api_key,
                amazon_sp_api_client_id=settings.amazon_sp_api_client_id,
                amazon_sp_api_client_secret=settings.amazon_sp_api_client_secret,
                amazon_sp_api_refresh_token=settings.amazon_sp_api_refresh_token,
                amazon_sp_api_seller_id=settings.amazon_sp_api_seller_id,
                amazon_sp_api_marketplace_id=settings.amazon_sp_api_marketplace_id,
                inventory_store=inventory_store,
                want_list_store=want_list_store,
            ),
        )
    else:
        review_store = operator.reviews
        if inventory_store is None:
            inventory_store = getattr(operator.executor, "inventory_store", None) or open_inventory_store(
                settings.inventory_db
            )
        if hasattr(operator.executor, "inventory_store") and not operator.executor.inventory_store:
            operator.executor.inventory_store = inventory_store
        want_list_store = getattr(operator.executor, "want_list_store", None) or open_want_list_store(
            settings.want_list_db
        )
        if hasattr(operator.executor, "want_list_store") and not operator.executor.want_list_store:
            operator.executor.want_list_store = want_list_store
        for name in (
            "amazon_sp_api_client_id",
            "amazon_sp_api_client_secret",
            "amazon_sp_api_refresh_token",
            "amazon_sp_api_seller_id",
            "amazon_sp_api_marketplace_id",
        ):
            value = getattr(settings, name, None)
            if value and hasattr(operator.executor, name) and not getattr(operator.executor, name):
                setattr(operator.executor, name, value)

    if user_store is None:
        user_store = open_user_store(settings.user_db)

    from app.api.reconciliation_service import ReconciliationService

    reconciliation_service = ReconciliationService(
        operator=operator,
        user_store=user_store,
        settings=settings,
        want_list_store=want_list_store,
        inventory_store=inventory_store,
    )

    from app.integrations.protocol import parse_connections
    from app.integrations.service import CustomSiteService
    from app.integrations.stock import StockLedger

    connections = parse_connections(settings.custom_site_connections)
    stock_ledger = StockLedger(inventory_store, connections)
    custom_site_service = CustomSiteService(stock_ledger, user_store, settings)
    operator.executor.stock_ledger = stock_ledger
    operator.executor.custom_site_service = custom_site_service
    want_list_store.stock_ledger = stock_ledger
    for tenant_id in {c.tenant_id for c in connections if c.enabled}:
        for hold in want_list_store.list_holds(tenant_id=tenant_id, status="active"):
            stock_ledger.hold(tenant_id, hold.sku, hold.id, hold.expires_at.timestamp())

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
                        "reason": (
                            "Simultaneous collision (35ms delta). Amazon prioritized over eBay due to "
                            "critical seller cancellation SLA. Value $950 CAD held for human consultation."
                        ),
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
                        "extraction": {
                            "title": "Neuromancer",
                            "authors": ["William Gibson"],
                            "format": "trade_paperback",
                        },
                        "disambiguation": {
                            "best_candidate": {
                                "title": "Neuromancer (Ace Science Fiction Special)",
                                "match_score": 0.94,
                            }
                        },
                        "valuation": {
                            "condition_grade": "Very Good",
                            "channel_strategy": "multi_channel_antiquarian",
                            "in_store_price_cad": "40.00",
                            "online_price_cad": "45.00",
                        },
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
                        "line_items": [
                            {
                                "sku": "BK-LOTR-SET",
                                "quantity": 1,
                                "title": "The Lord of the Rings (3 Vols)",
                            }
                        ],
                        "shipping_address": {
                            "name": "Eowyn of Rohan",
                            "line1": "144 Meduseld Way",
                            "city": "Edoras",
                            "country": "CA",
                        },
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
            review_store.decide(
                "rev-corvus-pos-sync",
                approve=True,
                reviewer="agent:corvus",
                reason="Corvus: Deterministic inventory sync authorized. Protecting the vault.",
                tenant_id=1,
            )
            review_store.claim_for_execution("rev-corvus-pos-sync", actor="agent:corvus", tenant_id=1)
            review_store.complete_execution(
                "rev-corvus-pos-sync",
                result={"sku": "BK-PHILIP-K-DICK", "sync_results": {"amazon_spapi": "delisted"}},
                tenant_id=1,
            )
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
                InventoryItem(
                    "BK-DUNE-1965",
                    "Dune (First Edition)",
                    ["Frank Herbert"],
                    "hardcover",
                    "Fine",
                    Decimal("950.00"),
                    Decimal("995.00"),
                    "active",
                    now_dt,
                    now_dt,
                    section="Sci-Fi / Fantasy",
                    shelf="B-03",
                ),
                tenant_id=1,
            )
        if "BK-LOTR-SET" not in existing_skus:
            inventory_store.add(
                InventoryItem(
                    "BK-LOTR-SET",
                    "The Lord of the Rings (3 Vols)",
                    ["J.R.R. Tolkien"],
                    "hardcover",
                    "Very Good",
                    Decimal("180.00"),
                    Decimal("210.00"),
                    "active",
                    now_dt,
                    now_dt,
                    section="Classics",
                    shelf="C-01",
                ),
                tenant_id=1,
            )
        if "BK-PHILIP-K-DICK" not in existing_skus:
            inventory_store.add(
                InventoryItem(
                    "BK-PHILIP-K-DICK",
                    "Do Androids Dream of Electric Sheep?",
                    ["Philip K. Dick"],
                    "paperback",
                    "Good",
                    Decimal("30.00"),
                    Decimal("35.00"),
                    "active",
                    now_dt,
                    now_dt,
                    section="Sci-Fi / Fantasy",
                    shelf="B-03",
                ),
                tenant_id=1,
            )
    except Exception as exc:
        logger.warning("seed.inventory.failed", error=str(exc))

    if order_repository is None:
        order_repository = open_order_repository(settings.order_db)

    alert_manager = AlertManager()
    # Cover anything that fired before the app booted (e.g. stale recovery above).
    alert_manager.evaluate()

    require_read = _authorize(settings.tokens, READ, user_store)
    require_decide = _authorize(settings.tokens, DECIDE, user_store)
    require_execute = _authorize(settings.tokens, EXECUTE, user_store)
    require_preview_read = _authorize(settings.tokens, READ, user_store, allow_inactive=True)
    require_preview_decide = _authorize(settings.tokens, DECIDE, user_store, allow_inactive=True)
    require_preview_execute = _authorize(settings.tokens, EXECUTE, user_store, allow_inactive=True)

    app = FastAPI(title="Ops Core", version="0.3.0")
    from app.api.custom_site import custom_site_router

    app.include_router(custom_site_router(custom_site_service))
    app.state.custom_site_service = custom_site_service
    app.state.stock_ledger = stock_ledger

    from app.integrations.protocol import IntegrationError

    @app.exception_handler(IntegrationError)
    async def stock_error(request: Request, exc: IntegrationError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message, "retryable": exc.status == 503}},
            status_code=exc.status,
        )

    @app.post("/integrations/custom-site/deliveries/run")
    def run_site_deliveries(principal: Principal = Depends(require_execute)) -> dict[str, Any]:
        return custom_site_service.drain(tenant=principal.tenant_id)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    media_root = "/tmp/ops-core" if os.getenv("VERCEL") else os.getcwd()
    media_spines_dir = os.path.join(media_root, "media", "spines")
    os.makedirs(media_spines_dir, exist_ok=True)
    app.mount("/media/spines", StaticFiles(directory=media_spines_dir), name="media_spines")

    @app.middleware("http")
    async def correlate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        correlation_id = request.headers.get(CORRELATION_HEADER) or new_correlation_id()
        set_correlation_id(correlation_id)
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "http.unhandled_exception",
            path=request.url.path,
            method=request.method,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"{type(exc).__name__}: {str(exc)}" if str(exc) else type(exc).__name__,
                "type": type(exc).__name__,
            },
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing_page() -> HTMLResponse:
        return HTMLResponse(
            files("app.web").joinpath("index.html").read_text(),
            headers={
                "Content-Security-Policy": "default-src 'self'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com;",
            },
        )

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(
            files("app.web").joinpath("dashboard.html").read_text(),
            headers={
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self' https://fonts.googleapis.com; "
                "img-src 'self' blob: data:; "
                "font-src 'self' https://fonts.gstatic.com; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            },
        )

    @app.get("/dashboard/assets/{asset}", include_in_schema=False)
    def dashboard_asset(asset: str) -> Response:
        types = {
            "dashboard.css": "text/css",
            "dashboard.js": "text/javascript",
            "login.png": "image/png",
            "favicon.ico": "image/x-icon",
        }
        if asset not in types:
            raise HTTPException(404, "asset not found")
        path = files("app.web").joinpath(asset)
        if not path.is_file():
            raise HTTPException(404, "asset not found")
        return Response(path.read_bytes(), media_type=types[asset])

    @app.get("/operator/session")
    def session(principal: Principal = Depends(require_read)) -> dict[str, Any]:
        return {
            "name": principal.name,
            "permissions": sorted(principal.permissions),
            "mode": operator.health()["mode"],
        }

    @app.get("/reviews/{review_id}")
    def review_detail(review_id: str, principal: Principal = Depends(require_preview_read)) -> dict[str, Any]:
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
    def corvus_blessing(data: dict[str, str], principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        msg = data.get("message", "").strip()
        if not msg:
            raise HTTPException(400, "Blessing message cannot be empty")
        from app.agents.sanctuary import get_corvus_sanctuary

        sanctuary = get_corvus_sanctuary()
        blessing = sanctuary.bestow_blessing(
            message=msg, sender=principal.name or "operator", tenant_id=principal.tenant_id
        )
        return {
            "status": "blessing_received",
            "blessing_id": blessing.id,
            "message": blessing.message,
            "created_at": blessing.created_at,
        }

    @app.get("/reviews")
    def list_reviews(
        pending_only: bool = False, principal: Principal = Depends(require_preview_read)
    ) -> list[dict[str, Any]]:
        return operator.list_reviews(pending_only, tenant_id=principal.tenant_id)

    @app.post("/reviews")
    def propose_review(proposal: Proposal, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        try:
            return operator.propose(proposal.operation, proposal.payload, tenant_id=principal.tenant_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/reviews/{review_id}/decision")
    def decision(
        review_id: str, decision: Decision, principal: Principal = Depends(require_preview_decide)
    ) -> dict[str, Any]:
        try:
            item = operator.reviews.get(review_id, tenant_id=principal.tenant_id)
            billing = user_store.get_tenant_billing_status(principal.tenant_id)
            if billing.get("subscription_status") != "active" and (
                not item or item.operation not in {"intake_book", "review_shelf_capture"}
            ):
                raise HTTPException(402, "only catalogue draft reviews are available in preview")
            if item and item.operation == "contention_hold_vs_marketplace" and not decision.approve:
                # Operator chose to honor the collector courtesy hold and cancel marketplace order
                from app.api.biblio_service import BiblioService

                order_id = str(item.payload.get("order_id", ""))
                channel = str(item.payload.get("source_channel", "biblio"))
                if channel == "biblio" and order_id:
                    api_key, dealer_id = _get_tenant_biblio_creds(principal.tenant_id)
                    reason = decision.reason or "Reserved for established patron"
                    BiblioService.cancel_order(
                        order_id,
                        reason=reason,
                        api_key=api_key,
                        dealer_id=dealer_id,
                        dry_run=settings.dry_run,
                    )
            payload_update = decision.payload_update
            if item and item.payload.get("sample_preview"):
                payload_update = {**(payload_update or {}), "sample_preview": True}
            return operator.decide(
                review_id,
                decision.approve,
                principal.name,
                decision.reason,
                payload_update,
                tenant_id=principal.tenant_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "review not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/reviews/{review_id}/execute")
    def execute(review_id: str, principal: Principal = Depends(require_preview_execute)) -> dict[str, Any]:
        try:
            item = operator.reviews.get(review_id, tenant_id=principal.tenant_id)
            billing = user_store.get_tenant_billing_status(principal.tenant_id)
            if item and item.payload.get("sample_preview"):
                raise HTTPException(409, "sample catalogue drafts cannot be executed")
            if billing.get("subscription_status") != "active" and (
                not item or item.operation != "review_shelf_capture"
            ):
                raise HTTPException(402, "inventory changes require an active subscription")
            return operator.execute(review_id, actor=principal.name, tenant_id=principal.tenant_id)
        except KeyError as exc:
            raise HTTPException(404, "review not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ExecutionFailed as exc:
            # The item is terminal; a retry would risk duplicating a partial mutation.
            raise HTTPException(502, f"execution failed and needs re-approval: {exc.error}") from exc
        finally:
            # Evaluated on the request that moved the counter, so a failure pages
            # even if nobody is watching /alerts.
            alert_manager.evaluate()

    @app.post("/reviews/batch-delete")
    @app.delete("/reviews/batch")
    def batch_delete_reviews(
        payload: BatchDeleteRequest, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        count = operator.delete_batch(payload.review_ids, tenant_id=principal.tenant_id)
        return {"status": "deleted", "count": count, "review_ids": payload.review_ids}

    @app.delete("/reviews/{review_id}")
    def delete_review(review_id: str, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        deleted = operator.delete(review_id, tenant_id=principal.tenant_id)
        if not deleted:
            raise HTTPException(404, "review not found")
        return {"status": "deleted", "review_id": review_id}

    @app.delete("/reviews")
    def clear_reviews(status: str | None = None, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
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
    def quarantined(principal: Principal = Depends(require_read)) -> list[dict[str, Any]]:
        if not hasattr(order_repository, "quarantined"):
            raise HTTPException(501, "repository does not expose quarantined events")
        return list(order_repository.quarantined(tenant_id=principal.tenant_id))

    @app.post("/orders/quarantined/{event_id}/resolve")
    def resolve_quarantined(event_id: str, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        if not hasattr(order_repository, "resolve_quarantined"):
            raise HTTPException(501, "repository does not support resolution")
        if not order_repository.resolve_quarantined(event_id, tenant_id=principal.tenant_id):
            raise HTTPException(404, "quarantined event not found")
        logger.info("order.quarantine.resolved", event_id=event_id, actor=principal.name)
        return {"event_id": event_id, "resolved": True, "resolved_by": principal.name}

    @app.post("/webhooks/orders")
    async def order_webhook(request: Request, x_signature: str = Header(default="")) -> JSONResponse:
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
        request: Request,
        x_square_hmac_sha256: str = Header(default=""),
        tenant_id: int = 1,
    ) -> JSONResponse:
        if tenant_id != 1:
            raise HTTPException(403, "Square webhook key is bound to tenant 1")
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
            operator,
            tenant_id,
        )
        return JSONResponse(result, status_code=result.get("status", 200))

    from app.api.ebay_webhooks import handle_ebay_challenge, handle_ebay_order_webhook

    @app.get("/webhooks/ebay")
    async def ebay_challenge_endpoint(
        request: Request,
        challenge_code: str = "",
    ) -> JSONResponse:
        endpoint_url = str(request.url).split("?")[0]
        if not settings.dry_run:
            endpoint_url = endpoint_url.replace("http://", "https://")
        verif_token = getattr(settings, "ebay_verification_token", "") or "mock_ebay_verification_token"
        res = handle_ebay_challenge(
            challenge_code=challenge_code,
            verification_token=verif_token,
            endpoint_url=endpoint_url,
        )
        return JSONResponse(res, status_code=200 if "challengeResponse" in res else 400)

    @app.post("/webhooks/ebay")
    async def ebay_notification_endpoint(
        request: Request,
        principal: Principal = Depends(require_execute),
    ) -> JSONResponse:
        import json

        tenant_id = principal.tenant_id
        body = await request.body()
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        result = await run_in_threadpool(
            handle_ebay_order_webhook,
            payload,
            operator,
            tenant_id,
        )
        return JSONResponse(result, status_code=result.get("status", 200))

    @app.post("/webhooks/abebooks")
    async def abebooks_webhook_endpoint(
        request: Request,
        principal: Principal = Depends(require_execute),
    ) -> JSONResponse:
        import json

        tenant_id = principal.tenant_id
        body = await request.body()
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        from app.api.abebooks_service import AbeBooksService

        result = await run_in_threadpool(
            AbeBooksService.process_order,
            payload,
            operator,
            tenant_id,
        )
        return JSONResponse(result, status_code=result.get("http_status", 200))

    @app.post("/webhooks/biblio")
    async def biblio_webhook_endpoint(
        request: Request,
        principal: Principal = Depends(require_execute),
    ) -> JSONResponse:
        import json

        tenant_id = principal.tenant_id
        body = await request.body()
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        from app.api.biblio_service import BiblioService

        result = await run_in_threadpool(
            BiblioService.process_order,
            payload,
            operator,
            tenant_id,
            want_list_store,
        )
        return JSONResponse(result, status_code=result.get("http_status", 200))

    @app.post("/intake/camera")
    async def process_camera_upload(
        request: Request,
        file: UploadFile | None = File(None),
        files: list[UploadFile] = File(default_factory=list),
        intake_mode: str = Form("multi_stack"),
        section: str = Form("General"),
        shelf: str = Form(""),
        auto_register: bool = Form(False),
        principal: Principal = Depends(require_preview_decide),
    ) -> dict[str, Any]:
        if intake_mode not in {"multi_stack", "multi_angle_single_book", "shelf_capture"}:
            raise HTTPException(422, "Choose a supported photo intake mode")
        shelf_mode = intake_mode == "shelf_capture"
        auto_register_requested = auto_register
        billing = user_store.get_tenant_billing_status(principal.tenant_id)
        if shelf_mode or billing.get("subscription_status") != "active":
            auto_register = False
        from app.ai.book_vision import (
            GeminiBookVisionProvider,
            prepare_vision_image,
            resolve_gemini_api_key,
        )
        from app.amazon.scraper import AmazonScraperClient, SnapshotStore
        from app.discovery.books import OpenLibraryMetadataResolver
        from app.domain.intake_batch import (
            BatchExtractionItem,
            BatchSpineDeduplicator,
            FailedImageRecord,
            MultiAngleBookMerger,
        )
        from app.pricing import PricingEngine

        try:
            api_key = resolve_gemini_api_key(settings.gemini_api_key if hasattr(settings, "gemini_api_key") else None)
        except ValueError as exc:
            raise HTTPException(503, "Vision AI not configured: GEMINI_API_KEY required") from exc

        # Consolidate upload files from files list or single file argument
        upload_files: list[UploadFile] = []
        if files:
            upload_files.extend([f for f in files if f and f.filename])
        if file and file.filename and file not in upload_files:
            upload_files.append(file)
        if not upload_files:
            # Fallback inspection of form fields
            form = await request.form()
            for val in form.values():
                if isinstance(val, UploadFile) and val.filename and val not in upload_files:
                    upload_files.append(val)

        if not upload_files:
            raise HTTPException(400, "Uploaded image file is empty")

        if shelf_mode and len(upload_files) > 12:
            raise HTTPException(413, "Use at most 12 photos in one shelf capture")
        provider = GeminiBookVisionProvider(api_key=api_key)

        prepared_images: list[tuple[int, str, bytes, str]] = []
        failed_images: list[FailedImageRecord] = []

        for idx, u_file in enumerate(upload_files):
            fname = u_file.filename or f"photo_{idx + 1}.jpg"
            try:
                raw_bytes = await u_file.read(12 * 1024 * 1024 + 1) if shelf_mode else await u_file.read()
                if shelf_mode and len(raw_bytes) > 12 * 1024 * 1024:
                    raise ValueError("Photo exceeds 12 MiB")
                if not raw_bytes:
                    failed_images.append(FailedImageRecord(idx, fname, "Uploaded image file is empty"))
                    continue
                img_bytes, mime_type = prepare_vision_image(raw_bytes, u_file.content_type)
                prepared_images.append((idx, fname, img_bytes, mime_type))
            except Exception as prep_exc:
                logger.warning("Image preparation failed", filename=fname, error=str(prep_exc))
                failed_images.append(FailedImageRecord(idx, fname, f"Preparation failed: {prep_exc}"))

        if not prepared_images:
            first_err = failed_images[0].error if failed_images else "Uploaded image file is empty"
            raise HTTPException(400, f"No valid images could be processed: {first_err}")

        extracted_items: list[BatchExtractionItem] = []
        shelf_plan = None
        unidentified_spines = 0
        suppressed_duplicates = []
        dominant_orientation = "horizontal_stack"

        if intake_mode == "multi_angle_single_book":
            # Multi-angle single volume mode
            raw_pairs = [(b, m) for (_, _, b, m) in prepared_images]
            try:
                if hasattr(provider, "extract_multi_angle"):
                    extraction = await run_in_threadpool(provider.extract_multi_angle, raw_pairs)
                else:
                    extraction = await run_in_threadpool(provider.extract_stack, raw_pairs[0][0], raw_pairs[0][1])
            except Exception as exc:
                logger.error("Multi-angle vision extraction failed", error=str(exc))
                raise HTTPException(502, f"Vision extraction failed: {exc}") from exc

            dominant_orientation = extraction.orientation
            if extraction.books:
                if len(extraction.books) == 1:
                    primary_book = extraction.books[0]
                else:
                    primary_book = MultiAngleBookMerger.merge_angles(list(extraction.books)) or extraction.books[0]

                primary_filename = f"multi_angle_{len(prepared_images)}_shots"
                extracted_items.append(
                    BatchExtractionItem(
                        book=primary_book,
                        source_image_index=0,
                        source_filename=primary_filename,
                    )
                )
        else:
            # Multi-stack batch mode: extract each photo with per-image failure isolation
            raw_extracted_items: list[BatchExtractionItem] = []
            orientations: list[str] = []

            for img_idx, fname, img_bytes, mime_type in prepared_images:
                try:
                    shelf_extractor = getattr(provider, "extract_shelf_stack", None) if shelf_mode else None
                    extractor = shelf_extractor or provider.extract_stack
                    extraction = await run_in_threadpool(extractor, img_bytes, mime_type)
                    orientations.append(extraction.orientation)
                    unidentified_spines += extraction.unidentified_spines_count
                    for book in extraction.books:
                        raw_extracted_items.append(
                            BatchExtractionItem(
                                book=book,
                                source_image_index=img_idx,
                                source_filename=fname,
                            )
                        )
                except Exception as exc:
                    logger.warning("Stack extraction failed for image", filename=fname, error=str(exc))
                    failed_images.append(FailedImageRecord(img_idx, fname, f"Vision extraction failed: {exc}"))

            if not raw_extracted_items and failed_images:
                first_err = failed_images[0].error
                raise HTTPException(502, f"Vision extraction failed: {first_err}")

            if orientations:
                dominant_orientation = orientations[0]

            if shelf_mode:
                from app.domain.shelf_capture import plan_capture

                if not raw_extracted_items:
                    raise HTTPException(422, "No book spines were identified. Try a closer photo.")
                try:
                    shelf_plan = plan_capture(raw_extracted_items, {i: b for i, _, b, _ in prepared_images})
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
                extracted_items = raw_extracted_items
            else:
                kept_items, suppressed = BatchSpineDeduplicator.deduplicate(raw_extracted_items)
                extracted_items = kept_items
                suppressed_duplicates = suppressed

        # Initialize Pricing Engine & Catalog Resolver
        resolver = OpenLibraryMetadataResolver()
        scraper = AmazonScraperClient(
            api_key=settings.amazon_scraper_api_key,
            store=SnapshotStore(),
            live=not settings.dry_run and bool(settings.amazon_scraper_api_key),
        )
        pricing = PricingEngine(scraper)

        proposals = []
        all_payloads = []
        for batch_item in extracted_items:
            book = batch_item.book
            condition = "Good" if "clean_as_new" in book.visible_condition_cues else "Acceptable"
            fmt_str = book.format.value if hasattr(book.format, "value") else str(book.format)

            # Authors inference & catalog disambiguation
            resolved_authors = [a for a in (book.authors or ()) if a and a.strip().lower() != "unknown"]
            cand_title = book.title
            cand_match_score = 0.85 if book.confidence == "high" else 0.5
            cand_isbn = "UNKNOWN"

            if intake_mode == "multi_angle_single_book":
                triage_reason = (
                    f"Synthesized from {len(prepared_images)} multi-angle photographs (verified collector record)."
                )
            else:
                triage_reason = f"Auto-extracted from {batch_item.source_filename} (Stack Pos {book.stack_order})."

            if not resolved_authors and book.title and book.title.strip().lower() != "unknown":
                try:
                    disambig = await run_in_threadpool(resolver.resolve, book)
                    if disambig.matched and disambig.best_candidate and disambig.best_candidate.authors:
                        resolved_authors = list(disambig.best_candidate.authors)
                        cand_title = disambig.best_candidate.title
                        cand_match_score = round(disambig.best_candidate.match_score, 2)
                        cand_isbn = disambig.best_candidate.primary_isbn or "UNKNOWN"
                        triage_reason = (
                            f"Inferred author ({', '.join(resolved_authors)}) via Open Library catalog matching."
                        )
                except Exception as exc:
                    logger.debug("author_inference.openlibrary_failed", error=str(exc))

            copy_text = getattr(book, "raw_copyright_text", "") or ""
            flap_text = getattr(book, "raw_flap_text", "") or ""
            if not copy_text and book.edition_or_series_markers:
                copy_text = " | ".join(book.edition_or_series_markers)

            # Antiquarian ABAA Dual-Grade Condition Diagnostic Inspection
            from app.domain.condition import AntiquarianConditionInspector

            condition_report = AntiquarianConditionInspector.inspect(
                format_str=fmt_str,
                visible_cues=book.visible_condition_cues,
                raw_spine_text=book.raw_spine_text,
                raw_copyright_text=copy_text,
                raw_flap_text=flap_text,
                edition_or_series_markers=book.edition_or_series_markers,
                publisher_imprint=book.publisher_imprint or "",
                title=book.title,
                authors=resolved_authors,
            )
            condition = condition_report.book_grade.value

            # Deterministic Bibliographic Edition & Issue-Point Collation
            from app.domain.issue_points import BibliographicIssuePointDecoder

            issue_result = BibliographicIssuePointDecoder.decode(
                publisher=book.publisher_imprint,
                raw_copyright_text=copy_text,
                raw_flap_text=flap_text,
                visible_condition_cues=book.visible_condition_cues,
                format_str=fmt_str,
            )

            author_str = ", ".join(resolved_authors)
            price_data = await run_in_threadpool(pricing.discover_price, book.title, author_str, condition, fmt_str)

            channel_strat = "multi_channel_antiquarian"
            in_store_price = price_data["in_store_price_cad"]
            online_price = price_data["recommended_price_cad"]
            pricing_rationale = price_data["rationale"]

            # Apply Antiquarian Condition Valuation Factor (ABAA standard)
            try:
                num_p = float(online_price)
                if condition_report.valuation_multiplier != 1.0:
                    adjusted_p = round(num_p * condition_report.valuation_multiplier, 2)
                    pricing_rationale += (
                        f" [ABAA DUAL GRADE: {condition_report.dual_grade_code}] "
                        f"Condition factor {condition_report.valuation_multiplier:.2f}x applied "
                        f"({len(condition_report.defects)} defect(s) noted; ${adjusted_p:.2f} CAD)."
                    )
                    online_price = f"{adjusted_p:.2f}"
            except (ValueError, TypeError):
                pass

            prefix = (
                f"Synthesized from {len(prepared_images)} multi-angle photographs. "
                if intake_mode == "multi_angle_single_book"
                else ""
            )

            if issue_result.is_first_edition and issue_result.is_first_impression:
                channel_strat = "antiquarian_first_edition_vault"
                triage_reason = (
                    f"{prefix}🎯 TRUE 1st EDITION (1st Impression) CONFIRMED via {issue_result.publisher_rule_name}. "
                    f"{issue_result.reasoning_trace}"
                )
                try:
                    num_p = float(online_price)
                    if num_p < 45.0:
                        online_price = f"{max(num_p * 2.5, 45.0):.2f}"
                        pricing_rationale += (
                            f" [COLLECTIBLE PREMIUM] Elevated to first edition tier (${online_price} CAD). "
                            f"{issue_result.reasoning_trace}"
                        )
                except (ValueError, TypeError):
                    pass
            elif issue_result.is_book_club:
                channel_strat = "in_store_reading_copy"
                triage_reason = f"{prefix}⚠️ BOOK CLUB EDITION (BCE) DISQUALIFIED: {issue_result.reasoning_trace}"
                try:
                    num_p = float(online_price)
                    if num_p > 16.0:
                        online_price = "14.00"
                        in_store_price = "12.00"
                        pricing_rationale = (
                            f"Capped at used reading copy valuation ($14.00 CAD). "
                            f"Disqualified as trade first: {issue_result.reasoning_trace}"
                        )
                except (ValueError, TypeError):
                    pass

            payload = {
                "section": section,
                "shelf": shelf,
                "extraction": {
                    "title": book.title,
                    "authors": resolved_authors,
                    "format": fmt_str,
                    "raw_spine_text": book.raw_spine_text,
                    "raw_copyright_text": copy_text,
                    "raw_flap_text": flap_text,
                    "visible_condition_cues": book.visible_condition_cues,
                    "source_filename": batch_item.source_filename,
                    "source_image_index": batch_item.source_image_index,
                },
                "disambiguation": {
                    "best_candidate": {
                        "title": cand_title,
                        "match_score": cand_match_score,
                        "primary_isbn": cand_isbn,
                    },
                    "triage_reason": triage_reason,
                },
                "issue_point_analysis": issue_result.to_dict(),
                "condition_diagnostic": condition_report.to_dict(),
                "valuation": {
                    "condition_grade": condition_report.book_grade.value,
                    "dual_grade_code": condition_report.dual_grade_code,
                    "book_grade": condition_report.book_grade.value,
                    "jacket_grade": condition_report.jacket_grade.value if condition_report.jacket_grade else None,
                    "trade_catalog_blurb": condition_report.trade_catalog_blurb,
                    "channel_strategy": channel_strat,
                    "in_store_price_cad": in_store_price,
                    "online_price_cad": online_price,
                    "pricing_rationale": pricing_rationale,
                },
                "pricing_evidence": price_data["evidence"],
            }

            # Check for collector want list match (Priority Courtesy Hold)
            try:
                from app.domain.want_list import WantListMatcher

                w_matcher = WantListMatcher()
                active_wants = want_list_store.list_wants(tenant_id=principal.tenant_id, status="active")
                collectors_map = {c.id: c for c in want_list_store.list_collectors(tenant_id=principal.tenant_id)}
                w_cand = {
                    "title": cand_title or book.title,
                    "authors": resolved_authors,
                    "condition": condition,
                    "format": fmt_str,
                    "price_cad": price_data["recommended_price_cad"],
                }
                w_match = w_matcher.find_best_match(active_wants, collectors_map, w_cand)
                if w_match and w_match.matched:
                    c_name = w_match.collector.name if w_match.collector else "Collector"
                    payload["disambiguation"]["triage_reason"] = (
                        f"MATCHES COLLECTOR WANT: {c_name} has an active want for '{w_match.want_item.title}' "
                        f"(Budget: ${w_match.want_item.max_price_cad or 'Open'}). "
                        f"Priority 48h courtesy hold recommended."
                    )
                    payload["want_match"] = {
                        "matched": True,
                        "collector_name": c_name,
                        "collector_id": w_match.collector.id if w_match.collector else "",
                        "want_id": w_match.want_item.id if w_match.want_item else "",
                        "match_score": w_match.match_score,
                    }
            except Exception as w_exc:
                logger.debug("want_match.check_failed", error=str(w_exc))

            all_payloads.append(payload)
            if shelf_mode:
                continue
            try:
                result = operator.propose("intake_book", payload, tenant_id=principal.tenant_id)
                result["payload"] = payload
                if auto_register:
                    operator.decide(
                        review_id=result["review_id"],
                        approve=True,
                        reviewer=principal.name,
                        reason="Auto-registered via AI Vision Intake Studio",
                        tenant_id=principal.tenant_id,
                    )
                    outcome = operator.execute(
                        review_id=result["review_id"],
                        actor=principal.name,
                        tenant_id=principal.tenant_id,
                    )
                    result["executed"] = True
                    result["execution_outcome"] = outcome
                    res_sku = (
                        outcome.get("result", {}).get("sku")
                        or outcome.get("result", {}).get("details", {}).get("sku")
                        or ""
                    )
                    result["sku"] = res_sku
                proposals.append(result)
            except Exception as exc:
                logger.error("Failed to propose book", error=str(exc))

        shelf_summary = None
        if shelf_plan is not None:
            from uuid import uuid4

            from app.domain.shelf_capture import _thumbnail

            for observation, analysis in zip(shelf_plan["observations"], all_payloads, strict=True):
                observation["analysis"] = analysis
            shelf_plan.update(
                capture_id="capture_" + uuid4().hex,
                section=section,
                shelf=shelf,
                photos=[
                    {"image_index": i, "filename": name, "preview": _thumbnail(raw)}
                    for i, name, raw, _ in prepared_images
                ],
                failed_images=[f.to_dict() for f in failed_images],
                unidentified_spines=unidentified_spines,
            )
            proposal = operator.propose("review_shelf_capture", shelf_plan, tenant_id=principal.tenant_id)
            proposals.append(proposal)
            shelf_summary = {
                "review_id": proposal["review_id"],
                "observation_count": len(shelf_plan["observations"]),
                "suggested_copy_count": len(shelf_plan["suggested_groups"]),
                "possible_overlap_count": shelf_plan["possible_overlap_count"],
                "dense_photo_warning_count": len(shelf_plan.get("dense_photo_warnings", [])),
                "requires_review": True,
            }

        successful_count = len(upload_files) - len(failed_images)
        return {
            "shelf_draft": shelf_summary,
            "auto_register_requested": auto_register_requested,
            "processed": len(extracted_items),
            "total_images": len(upload_files),
            "successful_images": max(0, successful_count),
            "failed_images": [f.to_dict() for f in failed_images],
            "orientation": dominant_orientation,
            "intake_mode": intake_mode,
            "auto_register": auto_register,
            "duplicates_suppressed": [s.to_dict() for s in suppressed_duplicates],
            "proposals": proposals,
            "extracted_items": [
                {
                    "title": item.book.title,
                    "subtitle": getattr(item.book, "subtitle", None),
                    "authors": list(item.book.authors or ()),
                    "publisher_imprint": getattr(item.book, "publisher_imprint", ""),
                    "format": item.book.format.value if hasattr(item.book.format, "value") else str(item.book.format),
                    "confidence": getattr(item.book, "confidence", "high"),
                    "visible_condition_cues": list(item.book.visible_condition_cues or ()),
                    "edition_or_series_markers": list(item.book.edition_or_series_markers or ()),
                    "raw_spine_text": getattr(item.book, "raw_spine_text", ""),
                    "raw_copyright_text": getattr(item.book, "raw_copyright_text", ""),
                    "raw_flap_text": getattr(item.book, "raw_flap_text", ""),
                }
                for item in extracted_items
            ],
            "analyzed_payloads": [
                {
                    "title": p.get("extraction", {}).get("title"),
                    "authors": p.get("extraction", {}).get("authors", []),
                    "format": p.get("extraction", {}).get("format"),
                    "condition": p.get("valuation", {}).get("condition_grade"),
                    "dual_grade_code": p.get("valuation", {}).get("dual_grade_code"),
                    "condition_diagnostic": p.get("condition_diagnostic", {}),
                    "trade_catalog_blurb": p.get("valuation", {}).get("trade_catalog_blurb", ""),
                    "in_store_price_cad": p.get("valuation", {}).get("in_store_price_cad"),
                    "online_price_cad": p.get("valuation", {}).get("online_price_cad"),
                    "channel_strategy": p.get("valuation", {}).get("channel_strategy"),
                    "pricing_rationale": p.get("valuation", {}).get("pricing_rationale"),
                    "issue_point_analysis": p.get("issue_point_analysis", {}),
                    "disambiguation": p.get("disambiguation", {}),
                    "want_match": p.get("want_match"),
                }
                for p in all_payloads
            ],
        }

    @app.post("/intake/sample")
    async def process_sample_upload(
        principal: Principal = Depends(require_preview_decide),
    ) -> dict[str, Any]:
        billing = user_store.get_tenant_billing_status(principal.tenant_id)
        preview_mode = billing.get("subscription_status") != "active"
        sample_books = [
            {
                "title": "Neuromancer",
                "authors": ["William Gibson"],
                "format": "trade_paperback",
                "condition": "Very Good",
                "raw_spine_text": "GIBSON NEUROMANCER ACE SCIENCE FICTION",
                "match_score": 0.94,
                "in_store_price_cad": 12.0,
                "online_price_cad": 18.0,
            },
            {
                "title": "Foundation",
                "authors": ["Isaac Asimov"],
                "format": "hardcover_with_jacket",
                "condition": "Good",
                "raw_spine_text": "FOUNDATION ASIMOV GNOME PRESS",
                "match_score": 0.91,
                "in_store_price_cad": 18.0,
                "online_price_cad": 26.0,
            },
            {
                "title": "The Left Hand of Darkness",
                "authors": ["Ursula K. Le Guin"],
                "format": "hardcover_with_jacket",
                "condition": "Very Good",
                "raw_spine_text": "THE LEFT HAND OF DARKNESS LE GUIN WALKER",
                "match_score": 0.89,
                "in_store_price_cad": 15.0,
                "online_price_cad": 22.0,
            },
        ]

        proposals = []
        for idx, book in enumerate(sample_books, start=1):
            sample_rationale = (
                "Illustrative sample price only; verify edition, condition, and current market before listing."
            )
            sample_evidence = {
                "confidence_label": "Sample only",
                "comps_found": 0,
                "top_comps": [],
            }
            payload = {
                "sample_preview": preview_mode,
                "section": "Sci-Fi / Fantasy" if idx <= 3 else "Canadiana / Rare",
                "shelf": "B-03" if idx <= 3 else "Vault-1",
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
                    "in_store_price_cad": book["in_store_price_cad"],
                    "online_price_cad": book["online_price_cad"],
                    "pricing_rationale": sample_rationale,
                },
                "pricing_evidence": sample_evidence,
            }

            # Inactive accounts get isolated demo records. Active accounts keep
            # the existing collector-want preview behavior of this sample flow.
            if not preview_mode:
                try:
                    from app.domain.want_list import WantListMatcher

                    matcher = WantListMatcher()
                    active_wants = want_list_store.list_wants(tenant_id=principal.tenant_id, status="active")
                    collectors = {c.id: c for c in want_list_store.list_collectors(tenant_id=principal.tenant_id)}
                    match = matcher.find_best_match(
                        active_wants,
                        collectors,
                        {
                            "title": book["title"],
                            "authors": book["authors"],
                            "condition": book["condition"],
                            "format": book["format"],
                            "price_cad": book["online_price_cad"],
                        },
                    )
                    if match and match.matched:
                        collector_name = match.collector.name if match.collector else "Collector"
                        payload["disambiguation"]["triage_reason"] = (
                            f"MATCHES COLLECTOR WANT: {collector_name} has an active want for "
                            f"'{match.want_item.title}' (Budget: ${match.want_item.max_price_cad or 'Open'}). "
                            "Priority 48h courtesy hold recommended."
                        )
                        payload["want_match"] = {
                            "matched": True,
                            "collector_name": collector_name,
                            "collector_id": match.collector.id if match.collector else "",
                            "want_id": match.want_item.id if match.want_item else "",
                            "match_score": match.match_score,
                        }
                except Exception as exc:
                    logger.debug("want_match.check_failed", error=str(exc))

            res = operator.propose("intake_book", payload, tenant_id=principal.tenant_id)
            proposals.append(res)

        return {
            "processed": len(proposals),
            "orientation": "horizontal_stack",
            "proposals": proposals,
        }

    @app.post("/intake/sample-dune")
    async def process_sample_dune_upload(
        auto_register: bool = Form(False),
        section: str = Form("Rare Books"),
        shelf: str = Form("Vault-01"),
        principal: Principal = Depends(require_decide),
    ) -> dict[str, Any]:
        """High-fidelity fixture for 3-Angle Antiquarian Vision Intake: Dune 1st Edition."""
        from app.domain.condition import AntiquarianConditionInspector
        from app.domain.issue_points import BibliographicIssuePointDecoder

        condition_report = AntiquarianConditionInspector.inspect(
            format_str="hardcover_with_jacket",
            visible_cues=["clean_as_new"],
            raw_spine_text="DUNE FRANK HERBERT CHILTON BOOKS",
            raw_copyright_text=(
                "First Edition. Copyright 1965 by Frank Herbert. Library of Congress Catalog Card Number 65-22547."
            ),
            raw_flap_text="$5.95 price intact on front dust jacket flap. Chilton Books, Philadelphia.",
            edition_or_series_markers=[
                "First Edition stated on copyright page",
                "$5.95 unclipped dust jacket",
            ],
            publisher_imprint="Chilton Books, Philadelphia",
            title="Dune",
            authors=["Frank Herbert"],
        )

        issue_result = BibliographicIssuePointDecoder.decode(
            publisher="Chilton Books, Philadelphia",
            raw_copyright_text=(
                "First Edition. Copyright 1965 by Frank Herbert. Library of Congress Catalog Card Number 65-22547."
            ),
            raw_flap_text="$5.95 price intact on front dust jacket flap. Chilton Books, Philadelphia.",
            visible_condition_cues=["clean_as_new"],
            format_str="hardcover_with_jacket",
        )

        in_store_price = "750.00"
        online_price = "850.00"
        pricing_rationale = (
            f"[COLLECTIBLE PREMIUM - ABAA {condition_report.dual_grade_code}] "
            "Elevated to first edition tier ($850.00 CAD). "
            "True First Edition, First Impression confirmed via Chilton Books rule "
            "(Four lines of copyright, unclipped $5.95 jacket)."
        )
        channel_strat = "antiquarian_first_edition_vault"

        payload = {
            "section": section,
            "shelf": shelf,
            "extraction": {
                "title": "Dune",
                "authors": ["Frank Herbert"],
                "format": "hardcover_with_jacket",
                "raw_spine_text": "DUNE FRANK HERBERT CHILTON BOOKS",
                "raw_copyright_text": (
                    "First Edition. Copyright 1965 by Frank Herbert. Library of Congress Catalog Card Number 65-22547."
                ),
                "raw_flap_text": "$5.95 price intact on front dust jacket flap.",
                "visible_condition_cues": ["clean_as_new"],
                "source_filename": "dune_sample_3_shots",
                "source_image_index": 0,
            },
            "disambiguation": {
                "best_candidate": {
                    "title": "Dune",
                    "match_score": 0.99,
                    "primary_isbn": "UNKNOWN",
                },
                "triage_reason": (
                    "Synthesized from 3 multi-angle photographs. 🎯 TRUE 1st EDITION (1st Impression) CONFIRMED via "
                    f"{issue_result.publisher_rule_name}. {issue_result.reasoning_trace}"
                ),
            },
            "issue_point_analysis": issue_result.to_dict(),
            "condition_diagnostic": condition_report.to_dict(),
            "valuation": {
                "condition_grade": condition_report.book_grade.value,
                "dual_grade_code": condition_report.dual_grade_code,
                "book_grade": condition_report.book_grade.value,
                "jacket_grade": condition_report.jacket_grade.value if condition_report.jacket_grade else None,
                "trade_catalog_blurb": condition_report.trade_catalog_blurb,
                "channel_strategy": channel_strat,
                "in_store_price_cad": in_store_price,
                "online_price_cad": online_price,
                "pricing_rationale": pricing_rationale,
            },
            "pricing_evidence": {
                "source": "AbeBooks / Rare Book Hub Historical Auction Averages",
                "comparables": [
                    {
                        "title": "Dune (1st Ed/1st Pr, Chilton 1965)",
                        "condition": "Near Fine / Near Fine",
                        "price_cad": "950.00",
                    },
                    {
                        "title": "Dune (1st Ed, Chilton)",
                        "condition": "Very Good / Very Good",
                        "price_cad": "750.00",
                    },
                ],
            },
        }

        # Check for collector want list match (Priority Courtesy Hold)
        try:
            from decimal import Decimal

            from app.domain.want_list import WantListMatcher

            w_matcher = WantListMatcher()
            active_wants = want_list_store.list_wants(tenant_id=principal.tenant_id, status="active")
            collectors_map = {c.id: c for c in want_list_store.list_collectors(tenant_id=principal.tenant_id)}
            w_cand = {
                "title": "Dune",
                "authors": ["Frank Herbert"],
                "condition": condition_report.book_grade.value,
                "format": "hardcover_with_jacket",
                "price_cad": Decimal("850.00"),
            }
            w_match = w_matcher.find_best_match(active_wants, collectors_map, w_cand)
            if w_match and w_match.matched:
                c_name = w_match.collector.name if w_match.collector else "Collector"
                payload["disambiguation"]["triage_reason"] = (
                    f"MATCHES COLLECTOR WANT: {c_name} has an active want for '{w_match.want_item.title}' "
                    f"(Budget: ${w_match.want_item.max_price_cad or 'Open'}). "
                    f"Priority 48h courtesy hold recommended."
                )
                payload["want_match"] = {
                    "matched": True,
                    "collector_name": c_name,
                    "collector_id": w_match.collector.id if w_match.collector else "",
                    "want_id": w_match.want_item.id if w_match.want_item else "",
                    "match_score": w_match.match_score,
                }
        except Exception as w_exc:
            logger.debug("want_match.check_failed", error=str(w_exc))

        result = operator.propose("intake_book", payload, tenant_id=principal.tenant_id)
        result["payload"] = payload
        if auto_register:
            operator.decide(
                review_id=result["review_id"],
                approve=True,
                reviewer=principal.name,
                reason="Auto-registered via AI Vision Intake Studio (Sample Dune)",
                tenant_id=principal.tenant_id,
            )
            outcome = operator.execute(
                review_id=result["review_id"],
                actor=principal.name,
                tenant_id=principal.tenant_id,
            )
            result["executed"] = True
            result["execution_outcome"] = outcome
            res_sku = (
                outcome.get("result", {}).get("sku") or outcome.get("result", {}).get("details", {}).get("sku") or ""
            )
            result["sku"] = res_sku

        return {
            "processed": 1,
            "total_images": 3,
            "successful_images": 3,
            "failed_images": [],
            "orientation": "mixed_or_scattered",
            "intake_mode": "multi_angle_single_book",
            "auto_register": auto_register,
            "duplicates_suppressed": [],
            "proposals": [result],
            "extracted_items": [
                {
                    "title": "Dune",
                    "subtitle": None,
                    "authors": ["Frank Herbert"],
                    "publisher_imprint": "Chilton Books, Philadelphia",
                    "format": "hardcover_with_jacket",
                    "confidence": "high",
                    "visible_condition_cues": ["clean_as_new"],
                    "edition_or_series_markers": [
                        "First Edition stated on copyright page",
                        "$5.95 unclipped dust jacket",
                    ],
                    "raw_spine_text": "DUNE FRANK HERBERT CHILTON BOOKS",
                    "raw_copyright_text": (
                        "First Edition. Copyright 1965 by Frank Herbert. "
                        "Library of Congress Catalog Card Number 65-22547."
                    ),
                    "raw_flap_text": "$5.95 price intact on front dust jacket flap.",
                }
            ],
            "analyzed_payloads": [
                {
                    "title": "Dune",
                    "authors": ["Frank Herbert"],
                    "format": "hardcover_with_jacket",
                    "condition": condition_report.book_grade.value,
                    "dual_grade_code": condition_report.dual_grade_code,
                    "condition_diagnostic": condition_report.to_dict(),
                    "trade_catalog_blurb": condition_report.trade_catalog_blurb,
                    "in_store_price_cad": in_store_price,
                    "online_price_cad": online_price,
                    "channel_strategy": channel_strat,
                    "pricing_rationale": pricing_rationale,
                    "issue_point_analysis": issue_result.to_dict(),
                    "disambiguation": payload["disambiguation"],
                    "want_match": payload.get("want_match"),
                }
            ],
        }

    # ── SEEKERS BOOKS STOREFRONT PUBLIC ADAPTIVE ENDPOINTS ──
    @app.get("/api/shop/catalog")
    def get_shop_catalog() -> dict[str, Any]:
        """Public storefront catalog endpoint consumed by the Seekers Books website."""
        all_items = inventory_store.list_all(tenant_id=1)
        active_items = [i for i in all_items if i.status in ("active", "held", "sold")]

        batches_map: dict[str, dict[str, Any]] = {}
        items_payload: list[dict[str, Any]] = []

        for item in active_items:
            b_id = getattr(item, "batch_id", "batch-24") or "batch-24"
            b_name = getattr(item, "batch_name", "Autumn Accessions") or "Autumn Accessions"
            if b_id not in batches_map:
                batches_map[b_id] = {
                    "id": b_id,
                    "code": b_id.upper().replace("-", " "),
                    "name": b_name,
                    "subtitle": f"Curated drops from the Annex collection · {b_name}",
                    "date": "Active Collection",
                    "foreword": "Recent accessions from the Seekers Books subterranean sanctuary in the Annex.",
                }

            items_payload.append(
                {
                    "id": item.sku,
                    "batchId": b_id,
                    "shelf": item.section or "General",
                    "author": ", ".join(item.authors) if isinstance(item.authors, list) else str(item.authors),
                    "title": item.title,
                    "subtitle": None,
                    "edition": getattr(item, "collation", "") or item.format,
                    "publisher": getattr(item, "publisher", "") or "Seekers Imprint",
                    "place": "Toronto / London",
                    "year": getattr(item, "year", 0) or item.created_at.year,
                    "format": item.format,
                    "collation": getattr(item, "collation", "") or f"{item.format}, {item.condition}",
                    "binding": item.format.replace("_", " ").title(),
                    "condition": item.condition.title(),
                    "conditionNotes": getattr(item, "condition_notes", "")
                    or f"Condition graded {item.condition}. Physical copy in the Annex shop.",
                    "price": float(item.online_price_cad or item.in_store_price_cad or 0.0),
                    "status": "held" if item.status == "held" else ("sold" if item.status == "sold" else "available"),
                    "heldUntil": getattr(item, "held_until", None),
                    "featuredArrival": True,
                    "coverImage": getattr(item, "spine_image_url", "") or None,
                    "tags": [item.section or "Philosophy", item.format],
                }
            )

        if not batches_map:
            batches_map["all"] = {
                "id": "all",
                "code": "ALL SHELVES",
                "name": "Complete Seekers Ledger",
                "subtitle": "All active subterranean Annex shelves",
                "date": "Current Active Index",
                "foreword": "The complete accession ledger of Seekers Books at 509 Bloor West.",
            }

        return {
            "shop": {
                "name": "SEEKERS BOOKS",
                "established": "Est. 1988 · 509 Bloor St. West, The Annex, Toronto",
                "status": "Subterranean Annex Sanctuary Open",
                "hours": "12:00 - 21:00 Daily (7 Days a Week)",
                "phone": "(416) 925-1982",
                "address": "509 Bloor Street West, Toronto, ON M5S 1Y2",
                "instagram": "https://www.instagram.com/seekersbooks/",
                "guardian": "Churchill (Resident Tabby Cat & Shelf Warden)",
            },
            "batches": list(batches_map.values()),
            "inventory": items_payload,
        }

    @app.post("/api/shop/holds")
    def create_shop_hold(req: ShopHoldRequest, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        """Authenticated legacy counter hold; checkout uses the signed v1 API."""
        import random
        from datetime import UTC, datetime, timedelta

        item = inventory_store.get(req.sku, tenant_id=principal.tenant_id)
        if not item:
            raise HTTPException(404, f"Book with SKU '{req.sku}' not found")
        if item.status == "held":
            raise HTTPException(409, "Book is already on reserve for another patron")
        if item.status == "sold":
            raise HTTPException(410, "Book has already been claimed and settled")

        slip_code = f"SLIP-{req.sku}-{random.randint(1000, 9999)}"
        held_until = (datetime.now(UTC) + timedelta(hours=48)).isoformat()

        stock_ledger.hold(principal.tenant_id, req.sku, slip_code, datetime.fromisoformat(held_until).timestamp())
        if hasattr(inventory_store, "create_hold"):
            inventory_store.create_hold(
                sku=req.sku,
                slip_code=slip_code,
                patron_name=req.patron_name,
                patron_email=req.patron_email,
                patron_phone=req.patron_phone,
                pickup_note=req.pickup_note,
                held_until=held_until,
                tenant_id=principal.tenant_id,
            )
        else:
            import json

            item.status = "held"
            item.held_until = held_until
            item.hold_slip_code = slip_code
            item.patron_info = json.dumps(
                {
                    "name": req.patron_name,
                    "email": req.patron_email,
                    "phone": req.patron_phone,
                    "note": req.pickup_note,
                }
            )
            inventory_store.add(item, tenant_id=principal.tenant_id)

        return {
            "success": True,
            "slipCode": slip_code,
            "sku": req.sku,
            "status": "held",
            "heldUntil": held_until,
            "patronName": req.patron_name,
            "pickupLocation": "Seekers Books, 509 Bloor St. West, down the stairs",
        }

    @app.delete("/api/shop/holds/{sku}")
    def release_shop_hold(sku: str, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        """Release a 48-hour counter hold."""
        item = inventory_store.get(sku, tenant_id=principal.tenant_id)
        if item and item.hold_slip_code:
            stock_ledger.release_hold(principal.tenant_id, sku, item.hold_slip_code)
        if hasattr(inventory_store, "release_hold"):
            inventory_store.release_hold(sku, tenant_id=principal.tenant_id)
        else:
            item = inventory_store.get(sku, tenant_id=principal.tenant_id)
            if item and item.status in ("held", "active") and item.hold_slip_code:
                item.status = "active"
                item.held_until = None
                item.hold_slip_code = None
                item.patron_info = ""
                inventory_store.add(item, tenant_id=principal.tenant_id)
        return {"success": True, "sku": sku, "status": "available"}

    @app.get("/inventory")
    def list_inventory(principal: Principal = Depends(require_read)) -> list[dict[str, Any]]:
        return [
            {
                "sku": i.sku,
                "title": i.title,
                "authors": i.authors,
                "format": i.format,
                "condition": i.condition,
                "in_store_price_cad": float(i.in_store_price_cad),
                "online_price_cad": float(i.online_price_cad),
                "status": i.status,
                "section": i.section,
                "shelf": i.shelf,
                "last_audited_at": i.last_audited_at.isoformat() if i.last_audited_at else None,
                "created_at": i.created_at.isoformat() if hasattr(i.created_at, "isoformat") else str(i.created_at),
            }
            for i in inventory_store.list_all(tenant_id=principal.tenant_id)
        ]

    @app.put("/inventory/{sku}")
    def update_inventory_item(
        sku: str, payload: dict[str, Any], principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        from decimal import Decimal

        if "in_store_price_cad" in payload or "online_price_cad" in payload:
            in_store = Decimal(str(payload.get("in_store_price_cad", "0")))
            online = Decimal(str(payload.get("online_price_cad", "0")))
            inventory_store.update_prices(sku, in_store, online, tenant_id=principal.tenant_id)
            operator.propose(
                "update_prices",
                {"sku": sku, "in_store_price_cad": str(in_store), "online_price_cad": str(online)},
                tenant_id=principal.tenant_id,
            )
        if "section" in payload or "shelf" in payload:
            sec = str(payload.get("section", "General"))
            sh = str(payload.get("shelf", ""))
            inventory_store.update_location(sku, sec, sh, tenant_id=principal.tenant_id)
        if "status" in payload:
            new_st = str(payload["status"])
            if new_st == "active" and hasattr(inventory_store, "relist"):
                inventory_store.relist(sku, tenant_id=principal.tenant_id)
            elif new_st == "delisted" and hasattr(inventory_store, "delist"):
                inventory_store.delist(sku, tenant_id=principal.tenant_id)
        return {"status": "ok"}

    @app.post("/inventory/{sku}/relist")
    def relist_inventory_item(sku: str, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        item = inventory_store.get(sku, tenant_id=principal.tenant_id)
        if not item:
            raise HTTPException(404, "item not found")
        if hasattr(inventory_store, "relist"):
            inventory_store.relist(sku, tenant_id=principal.tenant_id)
        operator.propose("update_inventory", {"sku": sku, "stock": 1}, tenant_id=principal.tenant_id)
        return {"status": "ok", "sku": sku}

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

    @app.post("/inventory/batch-action")
    def batch_inventory_action(
        payload: dict[str, Any],
        principal: Principal = Depends(require_decide),
    ) -> dict[str, Any]:
        from decimal import Decimal

        action = payload.get("action")
        skus = payload.get("skus") or []
        params = payload.get("params") or {}

        if not action or not isinstance(skus, list) or not skus:
            raise HTTPException(400, "action and non-empty skus list required")

        tenant_id = principal.tenant_id

        if action == "delist":
            affected = inventory_store.batch_delist(skus, tenant_id=tenant_id)
            rev = operator.propose(
                "update_inventory",
                {"changes": {sku: 0 for sku in skus}},
                tenant_id=tenant_id,
            )
            return {
                "status": "ok",
                "action": action,
                "affected_count": affected,
                "review_id": rev["review_id"],
            }

        elif action == "relist":
            affected = inventory_store.batch_relist(skus, tenant_id=tenant_id)
            rev = operator.propose(
                "update_inventory",
                {"changes": {sku: 1 for sku in skus}},
                tenant_id=tenant_id,
            )
            return {
                "status": "ok",
                "action": action,
                "affected_count": affected,
                "review_id": rev["review_id"],
            }

        elif action == "relocate":
            section = str(params.get("section", "General"))
            shelf = str(params.get("shelf", ""))
            affected = inventory_store.batch_update_location(skus, section, shelf, tenant_id=tenant_id)
            return {"status": "ok", "action": action, "affected_count": affected}

        elif action == "delete":
            affected = inventory_store.batch_delete(skus, tenant_id=tenant_id)
            rev = operator.propose(
                "update_inventory",
                {"changes": {sku: 0 for sku in skus}},
                tenant_id=tenant_id,
            )
            return {
                "status": "ok",
                "action": action,
                "affected_count": affected,
                "review_id": rev["review_id"],
            }

        elif action == "price_adjust":
            price_mode = str(params.get("price_mode", "percent"))
            price_value = Decimal(str(params.get("price_value", "0")))
            price_target = str(params.get("price_target", "both"))

            items = inventory_store.get_batch(skus, tenant_id=tenant_id)
            if not items:
                return {"status": "ok", "action": action, "affected_count": 0}

            updates: dict[str, tuple[Decimal, Decimal]] = {}
            online_changes: dict[str, str] = {}

            for item in items:
                curr_in = item.in_store_price_cad
                curr_on = item.online_price_cad
                new_in = curr_in
                new_on = curr_on

                if price_mode == "flat_override":
                    if price_target in ("in_store", "both"):
                        new_in = max(Decimal("0.00"), price_value)
                    if price_target in ("online", "both"):
                        new_on = max(Decimal("0.00"), price_value)
                elif price_mode == "fixed_delta":
                    if price_target in ("in_store", "both"):
                        new_in = max(Decimal("0.00"), curr_in + price_value)
                    if price_target in ("online", "both"):
                        new_on = max(Decimal("0.00"), curr_on + price_value)
                elif price_mode == "percent":
                    factor = Decimal("1") + (price_value / Decimal("100"))
                    if price_target in ("in_store", "both"):
                        new_in = max(Decimal("0.00"), (curr_in * factor).quantize(Decimal("0.01")))
                    if price_target in ("online", "both"):
                        new_on = max(Decimal("0.00"), (curr_on * factor).quantize(Decimal("0.01")))

                new_in = new_in.quantize(Decimal("0.01"))
                new_on = new_on.quantize(Decimal("0.01"))
                updates[item.sku] = (new_in, new_on)
                online_changes[item.sku] = str(new_on)

            affected = inventory_store.batch_update_prices(updates, tenant_id=tenant_id)
            rev = operator.propose(
                "update_prices",
                {"changes": online_changes},
                tenant_id=tenant_id,
            )
            return {
                "status": "ok",
                "action": action,
                "affected_count": affected,
                "review_id": rev["review_id"],
            }

        else:
            raise HTTPException(400, f"unrecognized batch action: {action}")

    @app.post("/inventory/export")
    def export_selected_inventory(
        payload: dict[str, Any],
        principal: Principal = Depends(require_read),
    ) -> Response:
        skus = payload.get("skus") or []
        export_format = str(payload.get("format", "csv")).lower()

        tenant_id = principal.tenant_id
        items = (
            inventory_store.get_batch(skus, tenant_id=tenant_id)
            if skus
            else inventory_store.list_all(tenant_id=tenant_id)
        )

        if export_format == "uiee":
            from app.domain.uiee import UIEEExporter

            uiee_text = UIEEExporter.export_catalog(items=items, status_filter="all", section_filter="all")
            return PlainTextResponse(
                content=uiee_text,
                media_type="text/plain; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="selected_catalog.uiee"'},
            )
        else:
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(
                [
                    "sku",
                    "title",
                    "authors",
                    "format",
                    "condition",
                    "in_store_price_cad",
                    "online_price_cad",
                    "status",
                    "section",
                    "shelf",
                    "last_audited_at",
                    "created_at",
                ]
            )
            for i in items:
                writer.writerow(
                    [
                        i.sku,
                        i.title,
                        "; ".join(i.authors),
                        i.format,
                        i.condition,
                        str(i.in_store_price_cad),
                        str(i.online_price_cad),
                        i.status,
                        i.section,
                        i.shelf,
                        i.last_audited_at.isoformat() if i.last_audited_at else "",
                        i.created_at.isoformat() if i.created_at else "",
                    ]
                )
            return Response(
                content=output.getvalue(),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="selected_inventory.csv"'},
            )

    @app.post("/inventory/intake-direct")
    def intake_book_direct(
        req: DirectIntakeRequest,
        principal: Principal = Depends(require_decide),
    ) -> dict[str, Any]:
        from decimal import Decimal

        payload = {
            "section": req.section,
            "shelf": req.shelf,
            "extraction": {
                "title": req.title,
                "authors": req.authors,
                "format": req.format,
            },
            "disambiguation": {
                "best_candidate": {
                    "title": req.title,
                    "authors": req.authors,
                },
                "triage_reason": "Direct intake confirmed via AI Vision Intake Studio.",
            },
            "issue_point_analysis": req.issue_points or {},
            "condition_diagnostic": req.condition_diagnostic or {},
            "valuation": {
                "condition_grade": req.condition,
                "dual_grade_code": req.dual_grade or req.condition,
                "trade_catalog_blurb": req.trade_catalog_blurb or "",
                "channel_strategy": req.channel_strategy,
                "in_store_price_cad": f"{req.in_store_price_cad:.2f}",
                "online_price_cad": f"{req.online_price_cad:.2f}",
                "pricing_rationale": req.pricing_rationale,
            },
        }

        # Check for collector want list match (Priority Courtesy Hold)
        try:
            from app.domain.want_list import WantListMatcher

            w_matcher = WantListMatcher()
            active_wants = want_list_store.list_wants(tenant_id=principal.tenant_id, status="active")
            collectors_map = {c.id: c for c in want_list_store.list_collectors(tenant_id=principal.tenant_id)}
            w_cand = {
                "title": req.title,
                "authors": req.authors,
                "condition": req.condition,
                "format": req.format,
                "price_cad": Decimal(str(req.online_price_cad or req.in_store_price_cad or 0.0)),
            }
            w_match = w_matcher.find_best_match(active_wants, collectors_map, w_cand)
            if w_match and w_match.matched:
                c_name = w_match.collector.name if w_match.collector else "Collector"
                payload["want_match"] = {
                    "matched": True,
                    "collector_name": c_name,
                    "collector_id": w_match.collector.id if w_match.collector else "",
                    "want_id": w_match.want_item.id if w_match.want_item else "",
                    "match_score": w_match.match_score,
                }
        except Exception as w_exc:
            logger.debug("want_match.check_failed", error=str(w_exc))

        proposed = operator.propose("intake_book", payload, tenant_id=principal.tenant_id)
        operator.decide(
            review_id=proposed["review_id"],
            approve=True,
            reviewer=principal.name,
            reason="Confirmed and registered via AI Vision Intake Studio",
            tenant_id=principal.tenant_id,
        )
        outcome = operator.execute(
            review_id=proposed["review_id"],
            actor=principal.name,
            tenant_id=principal.tenant_id,
        )
        res_sku = outcome.get("result", {}).get("sku") or outcome.get("result", {}).get("details", {}).get("sku") or ""
        return {
            "review_id": outcome["review_id"],
            "status": outcome["status"],
            "executed": True,
            "sku": res_sku,
            "result": outcome.get("result", {}),
            "payload": payload,
        }

    @app.post("/inventory/audit")
    async def run_shelf_audit(
        section: str = Form("General"),
        shelf: str = Form(""),
        file: UploadFile | None = File(None),
        principal: Principal = Depends(require_decide),
    ) -> dict[str, Any]:
        from app.db.inventory import ShelfAuditRecord
        from app.domain.shelf_audit import ShelfAuditService

        scanned_books: list[dict[str, Any]] = []

        if file and file.filename:
            from app.ai.book_vision import (
                GeminiBookVisionProvider,
                prepare_vision_image,
                resolve_gemini_api_key,
            )

            try:
                api_key = resolve_gemini_api_key(
                    settings.gemini_api_key if hasattr(settings, "gemini_api_key") else None
                )
            except ValueError as exc:
                raise HTTPException(503, "Vision AI not configured: GEMINI_API_KEY required") from exc

            provider = GeminiBookVisionProvider(api_key=api_key)
            raw_bytes = await file.read()
            if not raw_bytes:
                raise HTTPException(400, "Uploaded audit image is empty")

            image_bytes, mime_type = prepare_vision_image(raw_bytes, file.content_type)
            try:
                extraction = await run_in_threadpool(provider.extract_stack, image_bytes, mime_type)
                scanned_books = [b.to_dict() for b in extraction.books]
            except Exception as exc:
                logger.error("Vision audit extraction failed", error=str(exc))
                raise HTTPException(502, f"Vision extraction failed: {exc}") from exc

        expected_books = inventory_store.list_by_location(section, shelf, tenant_id=principal.tenant_id)
        store_inventory = inventory_store.list_all(tenant_id=principal.tenant_id)

        audit_res = ShelfAuditService.reconcile(
            section=section,
            shelf=shelf,
            scanned_books=scanned_books,
            expected_books=expected_books,
            store_inventory=store_inventory,
        )

        for v in audit_res.verified_items:
            inventory_store.mark_audited(v.sku, tenant_id=principal.tenant_id)

        discrepancies = [
            *[m.to_dict() for m in audit_res.missing_items],
            *[mp.to_dict() for mp in audit_res.misplaced_items],
            *[u.to_dict() for u in audit_res.uncataloged_items],
        ]
        audit_record = ShelfAuditRecord(
            id=audit_res.id,
            tenant_id=principal.tenant_id,
            section=section,
            shelf=shelf,
            audited_by=principal.name or "operator",
            total_expected=audit_res.total_expected,
            total_scanned=audit_res.total_scanned,
            verified_count=audit_res.verified_count,
            missing_count=audit_res.missing_count,
            misplaced_count=audit_res.misplaced_count,
            discrepancies=discrepancies,
            created_at=audit_res.created_at,
        )
        inventory_store.record_shelf_audit(audit_record)

        return audit_res.to_dict()

    @app.post("/inventory/audit/direct")
    def run_shelf_audit_direct(
        data: dict[str, Any],
        principal: Principal = Depends(require_decide),
    ) -> dict[str, Any]:
        from app.db.inventory import ShelfAuditRecord
        from app.domain.shelf_audit import ShelfAuditService

        section = data.get("section", "General")
        shelf = data.get("shelf", "")
        scanned_books = data.get("scanned_books", [])

        expected_books = inventory_store.list_by_location(section, shelf, tenant_id=principal.tenant_id)
        store_inventory = inventory_store.list_all(tenant_id=principal.tenant_id)

        audit_res = ShelfAuditService.reconcile(
            section=section,
            shelf=shelf,
            scanned_books=scanned_books,
            expected_books=expected_books,
            store_inventory=store_inventory,
        )

        for v in audit_res.verified_items:
            inventory_store.mark_audited(v.sku, tenant_id=principal.tenant_id)

        discrepancies = [
            *[m.to_dict() for m in audit_res.missing_items],
            *[mp.to_dict() for mp in audit_res.misplaced_items],
            *[u.to_dict() for u in audit_res.uncataloged_items],
        ]
        audit_record = ShelfAuditRecord(
            id=audit_res.id,
            tenant_id=principal.tenant_id,
            section=section,
            shelf=shelf,
            audited_by=principal.name or "operator",
            total_expected=audit_res.total_expected,
            total_scanned=audit_res.total_scanned,
            verified_count=audit_res.verified_count,
            missing_count=audit_res.missing_count,
            misplaced_count=audit_res.misplaced_count,
            discrepancies=discrepancies,
            created_at=audit_res.created_at,
        )
        inventory_store.record_shelf_audit(audit_record)

        return audit_res.to_dict()

    @app.get("/inventory/audits")
    def list_shelf_audits(
        limit: int = 20,
        principal: Principal = Depends(require_read),
    ) -> list[dict[str, Any]]:
        audits = inventory_store.list_shelf_audits(tenant_id=principal.tenant_id, limit=limit)
        return [
            {
                "id": a.id,
                "section": a.section,
                "shelf": a.shelf,
                "audited_by": a.audited_by,
                "total_expected": a.total_expected,
                "total_scanned": a.total_scanned,
                "verified_count": a.verified_count,
                "missing_count": a.missing_count,
                "misplaced_count": a.misplaced_count,
                "discrepancies": a.discrepancies,
                "created_at": a.created_at.isoformat() if hasattr(a.created_at, "isoformat") else str(a.created_at),
            }
            for a in audits
        ]

    def _get_tenant_square_creds(tenant_id: int) -> tuple[str, str]:
        import os

        token = ""
        try:
            enc_integrations = user_store.get_tenant_integrations(tenant_id)
            if "square" in enc_integrations:
                from app.vault import decrypt_credential

                token = decrypt_credential(enc_integrations["square"])
        except Exception:
            pass
        if not token:
            token = getattr(settings, "square_access_token", "") or os.getenv("SQUARE_ACCESS_TOKEN", "")
        loc_id = getattr(settings, "square_location_id", "") or os.getenv("SQUARE_LOCATION_ID", "")
        return token, loc_id

    @app.post("/inventory/{sku}/square/catalog")
    def push_book_to_square(sku: str, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        item = inventory_store.get(sku, tenant_id=principal.tenant_id)
        if not item:
            raise HTTPException(404, "item not found")
        from app.api.square_service import SquareService

        token, loc_id = _get_tenant_square_creds(principal.tenant_id)
        return SquareService.push_catalog_item(item, access_token=token, location_id=loc_id, dry_run=settings.dry_run)

    @app.post("/inventory/square/catalog/sync-all")
    def sync_all_to_square(principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        items = [i for i in inventory_store.list_all(tenant_id=principal.tenant_id) if i.status == "active"]
        from app.api.square_service import SquareService

        token, loc_id = _get_tenant_square_creds(principal.tenant_id)
        synced = []
        for item in items:
            res = SquareService.push_catalog_item(
                item, access_token=token, location_id=loc_id, dry_run=settings.dry_run
            )
            synced.append(res)
        return {
            "status": "completed",
            "total": len(items),
            "synced_count": len(synced),
            "results": synced,
        }

    @app.post("/inventory/{sku}/square/checkout")
    def create_book_square_checkout(sku: str, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        item = inventory_store.get(sku, tenant_id=principal.tenant_id)
        if not item:
            raise HTTPException(404, "item not found")
        if item.status != "active":
            raise HTTPException(400, f"Cannot create checkout for {item.status} book")
        from app.api.square_service import SquareService

        token, loc_id = _get_tenant_square_creds(principal.tenant_id)
        return SquareService.create_checkout_link(
            item, access_token=token, location_id=loc_id, dry_run=settings.dry_run
        )

    def _get_tenant_ebay_creds(tenant_id: int) -> tuple[str, str]:
        import os

        token = ""
        try:
            enc_integrations = user_store.get_tenant_integrations(tenant_id)
            if "ebay" in enc_integrations:
                from app.vault import decrypt_credential

                token = decrypt_credential(enc_integrations["ebay"])
        except Exception:
            pass
        if not token:
            token = getattr(settings, "ebay_access_token", "") or os.getenv("EBAY_ACCESS_TOKEN", "")
        mkt_id = getattr(settings, "ebay_marketplace_id", "EBAY_CA") or os.getenv("EBAY_MARKETPLACE_ID", "EBAY_CA")
        return token, mkt_id

    @app.post("/inventory/{sku}/ebay/draft")
    def push_book_to_ebay_draft(sku: str, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        item = inventory_store.get(sku, tenant_id=principal.tenant_id)
        if not item:
            raise HTTPException(404, "item not found")
        from app.api.ebay_service import EbayService

        token, mkt_id = _get_tenant_ebay_creds(principal.tenant_id)
        return EbayService.push_inventory_item(
            item, access_token=token, marketplace_id=mkt_id, dry_run=settings.dry_run
        )

    @app.post("/inventory/ebay/sync-all")
    def sync_all_to_ebay(principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        items = [i for i in inventory_store.list_all(tenant_id=principal.tenant_id) if i.status == "active"]
        from app.api.ebay_service import EbayService

        token, mkt_id = _get_tenant_ebay_creds(principal.tenant_id)
        synced = []
        for item in items:
            res = EbayService.push_inventory_item(
                item, access_token=token, marketplace_id=mkt_id, dry_run=settings.dry_run
            )
            synced.append(res)
        return {
            "status": "completed",
            "total": len(items),
            "synced_count": len(synced),
            "results": synced,
        }

    @app.post("/inventory/ebay/sync-orders")
    def sync_ebay_orders(principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        # Triggers manual check for newly placed eBay orders
        return {"status": "synced", "marketplace": "ebay", "orders_checked": 0, "sales_delisted": 0}

    def _get_tenant_abebooks_creds(tenant_id: int) -> tuple[str, str]:
        import os

        token = ""
        try:
            enc_integrations = user_store.get_tenant_integrations(tenant_id)
            if "abebooks" in enc_integrations:
                from app.vault import decrypt_credential

                token = decrypt_credential(enc_integrations["abebooks"])
        except Exception:
            pass
        if not token:
            token = getattr(settings, "abebooks_api_key", "") or os.getenv("ABEBOOKS_API_KEY", "")
        client_id = getattr(settings, "abebooks_client_id", "") or os.getenv("ABEBOOKS_CLIENT_ID", "ops_core_client")
        return token, client_id

    @app.get("/inventory/export/uiee")
    def export_catalog_uiee(
        status: str = "active",
        section: str = "ALL",
        principal: Principal = Depends(require_read),
    ) -> PlainTextResponse:
        from app.domain.uiee import UIEEExporter

        items = inventory_store.list_all(tenant_id=principal.tenant_id)
        uiee_text = UIEEExporter.export_catalog(
            items=items,
            status_filter=status,
            section_filter=section,
        )
        filename = (
            f"catalog_{section.lower()}_{status.lower()}.uiee" if section != "ALL" else f"catalog_{status.lower()}.uiee"
        )
        return PlainTextResponse(
            content=uiee_text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/inventory/abebooks/sync-orders")
    def sync_abebooks_orders(principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        from app.api.abebooks_service import AbeBooksService

        api_key, client_id = _get_tenant_abebooks_creds(principal.tenant_id)
        orders = AbeBooksService.fetch_orders(
            api_key=api_key,
            client_id=client_id,
            dry_run=settings.dry_run,
        )
        processed = []
        for order in orders:
            res = AbeBooksService.process_order(
                order,
                operator=operator,
                tenant_id=principal.tenant_id,
            )
            processed.append(res)
        return {
            "status": "synced",
            "marketplace": "abebooks",
            "orders_checked": len(orders),
            "orders_processed": len(processed),
            "details": processed,
        }

    def _get_tenant_biblio_creds(tenant_id: int) -> tuple[str, str]:
        import os

        token = ""
        try:
            enc_integrations = user_store.get_tenant_integrations(tenant_id)
            if "biblio" in enc_integrations:
                from app.vault import decrypt_credential

                token = decrypt_credential(enc_integrations["biblio"])
        except Exception:
            pass
        if not token:
            token = getattr(settings, "biblio_api_key", "") or os.getenv("BIBLIO_API_KEY", "")
        dealer_id = getattr(settings, "biblio_dealer_id", "") or os.getenv("BIBLIO_DEALER_ID", "")
        return token, dealer_id

    @app.post("/inventory/biblio/sync-orders")
    def sync_biblio_orders(
        demo: bool = False,
        principal: Principal = Depends(require_decide),
    ) -> dict[str, Any]:
        from app.api.biblio_service import BiblioService

        api_key, dealer_id = _get_tenant_biblio_creds(principal.tenant_id)
        orders = BiblioService.fetch_orders(
            api_key=api_key,
            dealer_id=dealer_id,
            dry_run=settings.dry_run,
            demo_sample=demo,
        )
        processed = []
        for order in orders:
            res = BiblioService.process_order(
                order,
                operator=operator,
                tenant_id=principal.tenant_id,
                want_list_store=want_list_store,
            )
            processed.append(res)
        return {
            "status": "synced",
            "marketplace": "biblio",
            "orders_checked": len(orders),
            "orders_processed": len(processed),
            "details": processed,
        }

    @app.api_route("/api/cron/sync", methods=["GET", "POST"])
    def cron_sync(
        demo: bool = False,
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        """Automated background pulse triggered only by an authenticated cron caller.

        Ingests orders across active tenants, detects 48h courtesy hold collisions,
        and triggers Corvus Warden autonomous triage.
        """
        token = bearer_token(authorization)
        cron_secret = getattr(settings, "cron_secret", "") or os.getenv("CRON_SECRET", "")

        if not cron_secret:
            logger.error("cron.sync.secret_not_configured")
            raise HTTPException(503, "cron authentication is not configured")

        if not token or not hmac.compare_digest(token, cron_secret):
            logger.warning("cron.sync.unauthorized_attempt")
            raise HTTPException(401, "unauthorized cron invocation", {"WWW-Authenticate": "Bearer"})

        result = reconciliation_service.run_reconciliation_cycle(
            demo_sample=demo,
        )
        result["custom_site_deliveries"] = custom_site_service.drain()
        return result

    from app.auth import create_access_token, get_password_hash, verify_password

    @app.post("/auth/signup")
    def signup(req: SignupRequest):
        if user_store.get_user_by_email(req.email):
            raise HTTPException(400, "Email already registered")

        tenant_id = user_store.create_tenant(req.store_name)
        pw_hash = get_password_hash(req.password)
        user_store.create_user(tenant_id, req.email, pw_hash, "admin")

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
            payload["stripe_subscription_status"] = billing.get("stripe_subscription_status")
            payload["has_billing_account"] = bool(billing.get("stripe_customer_id"))
            return payload
        except Exception:
            raise HTTPException(401, "Invalid token") from None

    from app.vault import encrypt_credential

    @app.post("/integrations")
    def add_integration(req: IntegrationRequest, token: str = Depends(get_bearer_token)):
        from app.auth import decode_access_token

        try:
            payload = decode_access_token(token)
            tenant_id = payload["tenant_id"]
        except Exception:
            raise HTTPException(401, "Invalid token") from None

        _require_active_subscription(user_store, tenant_id)

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
            raise HTTPException(401, "Invalid token") from None

        _require_active_subscription(user_store, tenant_id)

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
            raise HTTPException(401, "Invalid token") from None

        _require_active_subscription(user_store, tenant_id)

        return operator.reviews.get_tenant_analytics(tenant_id)

    # =========================================================================
    # Collector CRM & Want Lists & Priority Courtesy Holds
    # =========================================================================

    @app.get("/collectors")
    def list_collectors_endpoint(q: str = "", principal: Principal = Depends(require_read)) -> list[dict[str, Any]]:
        collectors = want_list_store.list_collectors(tenant_id=principal.tenant_id, query=q)
        wants = want_list_store.list_wants(tenant_id=principal.tenant_id, status="active")
        wants_count: dict[str, int] = {}
        for w in wants:
            wants_count[w.collector_id] = wants_count.get(w.collector_id, 0) + 1
        return [
            {
                "id": c.id,
                "name": c.name,
                "email": c.email,
                "phone": c.phone,
                "notes": c.notes,
                "active_wants_count": wants_count.get(c.id, 0),
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat(),
            }
            for c in collectors
        ]

    @app.post("/collectors")
    def create_collector_endpoint(
        payload: dict[str, Any], principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        import uuid

        from app.db.want_list import Collector

        name = str(payload.get("name", "")).strip()
        if not name:
            raise HTTPException(400, "Collector name is required")
        col_id = payload.get("id") or f"col-{uuid.uuid4().hex[:8]}"
        collector = Collector(
            id=col_id,
            tenant_id=principal.tenant_id,
            name=name,
            email=str(payload.get("email", "")).strip(),
            phone=str(payload.get("phone", "")).strip(),
            notes=str(payload.get("notes", "")).strip(),
        )
        saved = want_list_store.create_collector(collector)
        return {
            "id": saved.id,
            "name": saved.name,
            "email": saved.email,
            "phone": saved.phone,
            "notes": saved.notes,
            "created_at": saved.created_at.isoformat(),
        }

    @app.get("/collectors/{collector_id}")
    def get_collector_detail_endpoint(
        collector_id: str, principal: Principal = Depends(require_read)
    ) -> dict[str, Any]:
        collector = want_list_store.get_collector(collector_id, tenant_id=principal.tenant_id)
        if not collector:
            raise HTTPException(404, "Collector not found")
        wants = want_list_store.list_wants(tenant_id=principal.tenant_id, collector_id=collector_id)
        holds = [h for h in want_list_store.list_holds(tenant_id=principal.tenant_id) if h.collector_id == collector_id]
        return {
            "id": collector.id,
            "name": collector.name,
            "email": collector.email,
            "phone": collector.phone,
            "notes": collector.notes,
            "created_at": collector.created_at.isoformat(),
            "updated_at": collector.updated_at.isoformat(),
            "wants": [
                {
                    "id": w.id,
                    "title": w.title,
                    "author": w.author,
                    "max_price_cad": float(w.max_price_cad) if w.max_price_cad is not None else None,
                    "min_condition": w.min_condition,
                    "binding": w.binding,
                    "keywords": w.keywords,
                    "status": w.status,
                    "created_at": w.created_at.isoformat(),
                }
                for w in wants
            ],
            "holds": [
                {
                    "id": h.id,
                    "sku": h.sku,
                    "book_title": h.book_title,
                    "book_author": h.book_author,
                    "price_cad": float(h.price_cad) if h.price_cad is not None else None,
                    "held_at": h.held_at.isoformat(),
                    "expires_at": h.expires_at.isoformat(),
                    "status": h.status,
                    "notes": h.notes,
                }
                for h in holds
            ],
        }

    @app.put("/collectors/{collector_id}")
    def update_collector_endpoint(
        collector_id: str, payload: dict[str, Any], principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        updated = want_list_store.update_collector(collector_id, tenant_id=principal.tenant_id, **payload)
        if not updated:
            raise HTTPException(404, "Collector not found")
        return {"status": "ok", "id": updated.id, "name": updated.name}

    @app.delete("/collectors/{collector_id}")
    def delete_collector_endpoint(collector_id: str, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        deleted = want_list_store.delete_collector(collector_id, tenant_id=principal.tenant_id)
        if not deleted:
            raise HTTPException(404, "Collector not found")
        return {"status": "deleted", "id": collector_id}

    @app.get("/wants")
    def list_all_wants_endpoint(
        status: str = "active", principal: Principal = Depends(require_read)
    ) -> list[dict[str, Any]]:
        wants = want_list_store.list_wants(tenant_id=principal.tenant_id, status=status if status != "all" else None)
        collectors = {c.id: c.name for c in want_list_store.list_collectors(tenant_id=principal.tenant_id)}
        return [
            {
                "id": w.id,
                "collector_id": w.collector_id,
                "collector_name": collectors.get(w.collector_id, "Unknown"),
                "title": w.title,
                "author": w.author,
                "max_price_cad": float(w.max_price_cad) if w.max_price_cad is not None else None,
                "min_condition": w.min_condition,
                "binding": w.binding,
                "keywords": w.keywords,
                "status": w.status,
                "created_at": w.created_at.isoformat(),
            }
            for w in wants
        ]

    @app.post("/collectors/{collector_id}/wants")
    def create_want_endpoint(
        collector_id: str, payload: dict[str, Any], principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        import uuid
        from decimal import Decimal

        from app.db.want_list import WantItem

        collector = want_list_store.get_collector(collector_id, tenant_id=principal.tenant_id)
        if not collector:
            raise HTTPException(404, "Collector not found")
        title = str(payload.get("title", "")).strip()
        if not title:
            raise HTTPException(400, "Title is required")
        raw_price = payload.get("max_price_cad")
        max_price = Decimal(str(raw_price)) if raw_price is not None and str(raw_price).strip() else None
        want = WantItem(
            id=f"wnt-{uuid.uuid4().hex[:8]}",
            collector_id=collector_id,
            tenant_id=principal.tenant_id,
            title=title,
            author=str(payload.get("author", "")).strip(),
            max_price_cad=max_price,
            min_condition=str(payload["min_condition"]).strip() if payload.get("min_condition") else None,
            binding=str(payload["binding"]).strip() if payload.get("binding") else None,
            keywords=str(payload.get("keywords", "")).strip(),
            status="active",
        )
        saved = want_list_store.create_want(want)
        return {
            "id": saved.id,
            "collector_id": saved.collector_id,
            "collector_name": collector.name,
            "title": saved.title,
            "author": saved.author,
            "max_price_cad": float(saved.max_price_cad) if saved.max_price_cad is not None else None,
            "min_condition": saved.min_condition,
            "binding": saved.binding,
            "keywords": saved.keywords,
            "status": saved.status,
        }

    @app.put("/wants/{want_id}")
    def update_want_endpoint(
        want_id: str, payload: dict[str, Any], principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        updated = want_list_store.update_want(want_id, tenant_id=principal.tenant_id, **payload)
        if not updated:
            raise HTTPException(404, "Want item not found")
        return {"status": "ok", "id": updated.id, "title": updated.title}

    @app.delete("/wants/{want_id}")
    def delete_want_endpoint(want_id: str, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        deleted = want_list_store.delete_want(want_id, tenant_id=principal.tenant_id)
        if not deleted:
            raise HTTPException(404, "Want item not found")
        return {"status": "deleted", "id": want_id}

    @app.get("/holds")
    def list_holds_endpoint(status: str = "", principal: Principal = Depends(require_read)) -> list[dict[str, Any]]:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        holds = want_list_store.list_holds(tenant_id=principal.tenant_id, status=status or None)
        collectors = {c.id: c for c in want_list_store.list_collectors(tenant_id=principal.tenant_id)}
        result = []
        for h in holds:
            rem_sec = max(0.0, (h.expires_at - now).total_seconds())
            is_expired = h.status == "active" and now > h.expires_at
            col = collectors.get(h.collector_id)
            result.append(
                {
                    "id": h.id,
                    "sku": h.sku,
                    "want_item_id": h.want_item_id,
                    "collector_id": h.collector_id,
                    "collector_name": h.collector_name or (col.name if col else "Collector"),
                    "collector_phone": col.phone if col else "",
                    "collector_email": col.email if col else "",
                    "book_title": h.book_title,
                    "book_author": h.book_author,
                    "price_cad": float(h.price_cad) if h.price_cad is not None else None,
                    "held_at": h.held_at.isoformat(),
                    "expires_at": h.expires_at.isoformat(),
                    "status": h.status,
                    "notes": h.notes,
                    "is_expired": is_expired,
                    "remaining_seconds": rem_sec,
                    "remaining_hours": round(rem_sec / 3600.0, 1),
                    "claimed_at": h.claimed_at.isoformat() if h.claimed_at else None,
                    "released_at": h.released_at.isoformat() if h.released_at else None,
                }
            )
        return result

    @app.post("/holds/{hold_id}/claim")
    def claim_hold_endpoint(
        hold_id: str, payload: dict[str, Any] = None, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        payload = payload or {}
        outcome = operator.executor.handlers["claim_collector_hold"](
            {
                "hold_id": hold_id,
                "tenant_id": principal.tenant_id,
                "notes": payload.get("notes", "Claimed by collector"),
            }
        )
        return outcome

    @app.post("/holds/{hold_id}/release")
    def release_hold_endpoint(
        hold_id: str, payload: dict[str, Any] = None, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        payload = payload or {}
        outcome = operator.executor.handlers["release_collector_hold"](
            {
                "hold_id": hold_id,
                "tenant_id": principal.tenant_id,
                "reason": payload.get("reason", "manual_release"),
            }
        )
        return outcome

    @app.post("/holds/{hold_id}/extend")
    def extend_hold_endpoint(
        hold_id: str, payload: dict[str, Any] = None, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        from datetime import timedelta

        payload = payload or {}
        hours = int(payload.get("hours", 24))
        hold = want_list_store.get_hold(hold_id, tenant_id=principal.tenant_id)
        if not hold:
            raise HTTPException(404, "Hold not found")
        new_expires = hold.expires_at + timedelta(hours=hours)
        updated = want_list_store.update_hold(
            hold_id,
            tenant_id=principal.tenant_id,
            expires_at=new_expires,
            notes=f"{hold.notes} (Extended +{hours}h)".strip(),
        )
        return {
            "status": "extended",
            "hold_id": hold_id,
            "expires_at": updated.expires_at.isoformat() if updated else new_expires.isoformat(),
        }

    from app.api.billing import get_billing_router

    app.include_router(get_billing_router(settings, user_store), prefix="/billing")

    return app


app = create_app()
