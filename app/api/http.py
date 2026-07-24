from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from app.api.operator import OperatorService
from app.api.tiktok_client import TikTokClient
from app.api.webhook_service import handle_order_webhook
from app.db.sqlite import SQLiteOrderRepository
from app.domain.audit import AuditLog
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewQueue


class Decision(BaseModel):
    approve: bool
    reviewer: str
    reason: str = ""


def create_app(
    operator: OperatorService | None = None, order_repository: Any | None = None
) -> FastAPI:
    operator = operator or OperatorService(
        ReviewQueue(),
        ApprovedActionExecutor(
            TikTokClient(
                os.getenv("TTS_ACCESS_TOKEN", ""), os.getenv("TTS_SHOP_ID", ""), dry_run=True
            ),
            AuditLog(),
        ),
    )
    order_repository = order_repository or SQLiteOrderRepository(os.getenv("ORDER_DB", ":memory:"))
    app = FastAPI(title="TikTok Ops", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return operator.health()

    @app.get("/reviews")
    def reviews(pending_only: bool = False) -> list[dict]:
        return operator.list_reviews(pending_only)

    @app.post("/reviews/{review_id}/decision")
    def decision(review_id: str, decision: Decision) -> dict:
        try:
            return operator.decide(review_id, decision.approve, decision.reviewer, decision.reason)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/reviews/{review_id}/execute")
    def execute(review_id: str) -> dict:
        try:
            return operator.execute(review_id)
        except (KeyError, PermissionError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/webhooks/orders")
    async def order_webhook(request: Request, x_signature: str = Header(default="")) -> dict:
        secret = os.getenv("TTS_WEBHOOK_SECRET", "")
        body = await request.body()
        return handle_order_webhook(body, x_signature, secret, order_repository)

    return app


app = create_app()
