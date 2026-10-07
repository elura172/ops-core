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
from app.db.factory import open_order_repository, open_review_store, open_inventory_store
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
            logger.warning("auth.token_rejected", permission=permission)
            raise HTTPException(401, "invalid token", {"WWW-Authenticate": "Bearer"})
        if not principal.can(permission):
            logger.warning("auth.forbidden", principal=principal.name, permission=permission)
            raise HTTPException(403, f"principal lacks {permission}")
        return principal

    return dependency


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
        inventory_store = open_inventory_store(settings.review_db)
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

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/dashboard", status_code=302)

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
    def review_detail(review_id: str, _: Principal = Depends(require_read)) -> dict[str, Any]:
        try:
            return operator.review_detail(review_id)
        except KeyError as exc:
            raise HTTPException(404, "review not found") from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return operator.health()

    @app.get("/reviews")
    def list_reviews(
        pending_only: bool = False, _: Principal = Depends(require_read)
    ) -> list[dict[str, Any]]:
        return operator.list_reviews(pending_only)

    class Proposal(BaseModel):
        operation: str
        payload: dict[str, Any]

    @app.post("/reviews")
    def propose_review(
        proposal: Proposal, _: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        try:
            return operator.propose(proposal.operation, proposal.payload)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/reviews/{review_id}/decision")
    def decision(
        review_id: str, decision: Decision, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        try:
            return operator.decide(review_id, decision.approve, principal.name, decision.reason, decision.payload_update)
        except KeyError as exc:
            raise HTTPException(404, "review not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/reviews/{review_id}/execute")
    def execute(review_id: str, principal: Principal = Depends(require_execute)) -> dict[str, Any]:
        try:
            return operator.execute(review_id, actor=principal.name)
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
        file: UploadFile = File(...), _: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        from app.ai.book_vision import GeminiBookVisionProvider, resolve_gemini_api_key
        from app.pricing import PricingEngine
        from app.amazon.scraper import AmazonScraperClient, SnapshotStore

        try:
            api_key = resolve_gemini_api_key(settings.gemini_api_key if hasattr(settings, "gemini_api_key") else None)
        except ValueError as exc:
            raise HTTPException(500, "Vision AI not configured") from exc

        provider = GeminiBookVisionProvider(api_key=api_key)
        image_bytes = await file.read()
        
        try:
            extraction = await run_in_threadpool(provider.extract_stack, image_bytes, file.content_type or "image/jpeg")
        except Exception as exc:
            raise HTTPException(502, f"Vision extraction failed: {exc}") from exc

        # Initialize Pricing Engine
        scraper = AmazonScraperClient(
            api_key=getattr(settings, "shippo_api_key", "mock"),
            store=SnapshotStore(),
            live=not settings.dry_run
        )
        pricing = PricingEngine(scraper)

        proposals = []
        for idx, book in enumerate(extraction.books):
            condition = "Good" if "clean_as_new" in book.visible_condition_cues else "Acceptable"
            # Run pricing
            price_data = await run_in_threadpool(pricing.discover_price, book.title, ", ".join(book.authors), condition)

            payload = {
                "extraction": {
                    "title": book.title,
                    "authors": book.authors,
                    "format": book.format,
                    "raw_spine_text": book.raw_spine_text,
                    "visible_condition_cues": book.visible_condition_cues,
                },
                "disambiguation": {
                    "best_candidate": {
                        "title": book.title,
                        "match_score": 0.85 if book.confidence == "high" else 0.5,
                        "primary_isbn": "UNKNOWN",
                    },
                    "triage_reason": f"Auto-extracted from stack image (Position {book.stack_order}).",
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
                result = operator.propose("intake_book", payload)
                proposals.append(result)
            except Exception as exc:
                logger.error("Failed to propose book", error=str(exc))

        return {
            "processed": len(extraction.books),
            "orientation": extraction.orientation,
            "proposals": proposals
        }

    
    @app.get("/inventory")
    def list_inventory(principal: Principal = Depends(require_read)) -> list[dict[str, Any]]:
        return [
            {
                "sku": i.sku, "title": i.title, "authors": i.authors, "format": i.format,
                "condition": i.condition, "in_store_price_cad": float(i.in_store_price_cad),
                "online_price_cad": float(i.online_price_cad), "status": i.status,
                "created_at": i.created_at.isoformat()
            }
            for i in inventory_store.list_all()
        ]

    @app.put("/inventory/{sku}")
    def update_inventory_item(sku: str, payload: dict[str, Any], principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        from decimal import Decimal
        in_store = Decimal(str(payload.get("in_store_price_cad", "0")))
        online = Decimal(str(payload.get("online_price_cad", "0")))
        inventory_store.update_prices(sku, in_store, online)
        operator.propose("update_prices", {"sku": sku, "in_store_price_cad": str(in_store), "online_price_cad": str(online)})
        return {"status": "ok"}

    @app.delete("/inventory/{sku}")
    def delist_inventory_item(sku: str, principal: Principal = Depends(require_decide)) -> dict[str, Any]:
        inventory_store.delist(sku)
        operator.propose("update_inventory", {"sku": sku, "stock": 0})
        return {"status": "ok"}


    
    from app.auth import get_password_hash, verify_password, create_access_token
    from app.db.users import UserStore

    user_store = UserStore("users.db")

    class SignupRequest(BaseModel):
        store_name: str
        email: str
        password: str

    class LoginRequest(BaseModel):
        email: str
        password: str

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
    def auth_me(token: str = Depends(bearer_token)):
        from app.auth import decode_access_token
        try:
            payload = decode_access_token(token)
            return payload
        except Exception:
            raise HTTPException(401, "Invalid token")


    from app.vault import encrypt_credential, decrypt_credential

    class IntegrationRequest(BaseModel):
        provider: str
        api_key: str

    @app.post("/integrations")
    def add_integration(req: IntegrationRequest, token: str = Depends(bearer_token)):
        from app.auth import decode_access_token
        try:
            payload = decode_access_token(token)
            tenant_id = payload["tenant_id"]
        except Exception:
            raise HTTPException(401, "Invalid token")
            
        encrypted = encrypt_credential(req.api_key)
        
        with user_store._lock:
            user_store.connection.execute(
                "INSERT OR REPLACE INTO tenant_integrations(tenant_id, provider, encrypted_credentials) VALUES (?, ?, ?)",
                (tenant_id, req.provider, encrypted)
            )
            
        return {"status": "ok", "provider": req.provider}

    @app.get("/integrations")
    def list_integrations(token: str = Depends(bearer_token)):
        from app.auth import decode_access_token
        try:
            payload = decode_access_token(token)
            tenant_id = payload["tenant_id"]
        except Exception:
            raise HTTPException(401, "Invalid token")
            
        with user_store._lock:
            rows = user_store.connection.execute(
                "SELECT provider FROM tenant_integrations WHERE tenant_id = ?",
                (tenant_id,)
            ).fetchall()
            
        return {"connected_providers": [row["provider"] for row in rows]}

    return app


app = create_app()
