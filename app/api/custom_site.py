from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.integrations.database import transaction
from app.integrations.protocol import (
    IntegrationError,
    OrderEvent,
    ReleaseRequest,
    ReservationRequest,
)

PREFIX = "/api/integrations/custom-site/v1"


def custom_site_router(service: Any) -> APIRouter:
    router = APIRouter(prefix=PREFIX, tags=["Custom bookshop"])

    async def handle(request: Request, model: Any, action: Callable[..., Any]) -> JSONResponse:
        request_id = "req_" + uuid4().hex
        headers = {"Cache-Control": "no-store"}
        try:
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > 256 * 1024:
                    raise IntegrationError(413, "body_too_large", "Request body exceeds 256 KiB.")
            raw_body = bytes(body)
            path = request.scope.get("raw_path", request.url.path.encode()).decode("ascii")
            c, eid = await run_in_threadpool(
                service.authenticate, request.method, path, request.url.query, request.headers, raw_body
            )
            if model:
                if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                    raise IntegrationError(400, "json_required", "Send application/json.")
                try:
                    parsed = json.loads(raw_body)
                except (ValueError, UnicodeDecodeError):
                    raise IntegrationError(400, "invalid_json", "The request body is not valid JSON.") from None
                try:
                    payload = model.model_validate(parsed)
                except ValidationError:
                    raise IntegrationError(
                        422, "invalid_payload", "The request does not match the API schema."
                    ) from None
                if isinstance(payload, OrderEvent) and payload.event_id != eid:
                    raise IntegrationError(422, "event_id_mismatch", "Header and body event IDs must match.")
                status, result = await run_in_threadpool(
                    service.mutate,
                    c,
                    eid,
                    request.method,
                    path,
                    raw_body,
                    lambda db: action(db, c, payload),
                )
            else:
                if body:
                    raise IntegrationError(400, "body_not_allowed", "GET requests must have an empty body.")

                def read() -> Any:
                    with transaction(service.ledger.inventory, c.tenant_id) as db:
                        return action(db, c, None)

                result = await run_in_threadpool(read)
                status = 200
            return JSONResponse(result, status_code=status, headers=headers)
        except IntegrationError as exc:
            if exc.status == 429:
                headers["Retry-After"] = "60"
            return JSONResponse(
                {
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "retryable": exc.status in (429, 503),
                    },
                    "request_id": request_id,
                },
                status_code=exc.status,
                headers=headers,
            )
        except Exception:
            return JSONResponse(
                {
                    "error": {
                        "code": "service_unavailable",
                        "message": "The integration is temporarily unavailable.",
                        "retryable": True,
                    },
                    "request_id": request_id,
                },
                status_code=503,
                headers=headers,
            )

    @router.post("/reservations", status_code=201)
    async def reserve(request: Request) -> JSONResponse:
        return await handle(request, ReservationRequest, lambda db, c, p: (201, service.ledger.reserve(db, c, p)))

    @router.post("/reservations/{reservation_id}/release")
    async def release(reservation_id: str, request: Request) -> JSONResponse:
        return await handle(
            request,
            ReleaseRequest,
            lambda db, c, p: (200, service.ledger.release(db, c, reservation_id)),
        )

    @router.post("/order-events", status_code=202)
    async def order_event(request: Request) -> JSONResponse:
        return await handle(request, OrderEvent, lambda db, c, p: (202, service.ledger.order(db, c, p)))

    @router.get("/order-events/{event_id}")
    async def event_status(event_id: str, request: Request) -> JSONResponse:
        return await handle(request, None, lambda db, c, p: service.ledger.event_status(db, c, event_id))

    return router
