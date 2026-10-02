from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from importlib.resources import files
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
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
from app.db.factory import open_order_repository, open_review_store
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

    @app.post("/reviews/{review_id}/decision")
    def decision(
        review_id: str, decision: Decision, principal: Principal = Depends(require_decide)
    ) -> dict[str, Any]:
        try:
            return operator.decide(review_id, decision.approve, principal.name, decision.reason)
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
            handle_order_webhook, body, x_signature, settings.webhook_secret, order_repository
        )
        return JSONResponse(result.body, status_code=result.status)

    return app
