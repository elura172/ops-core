from __future__ import annotations

from typing import Any

from app.api.ebay_service import EbayService
from app.api.operator import OperatorService
from app.observability.logging import get_logger

logger = get_logger("ebay_webhook")


def handle_ebay_challenge(
    challenge_code: str,
    verification_token: str,
    endpoint_url: str,
) -> dict[str, str]:
    """Generates the challenge response for eBay endpoint verification."""
    if not challenge_code:
        logger.warning("ebay_webhook.challenge.missing_code")
        return {"error": "missing challenge_code"}

    response_hash = EbayService.verify_webhook_challenge(
        challenge_code=challenge_code,
        verification_token=verification_token,
        endpoint_url=endpoint_url,
    )
    logger.info("ebay_webhook.challenge.verified", endpoint_url=endpoint_url)
    return {"challengeResponse": response_hash}


def handle_ebay_order_webhook(
    payload: dict[str, Any],
    operator: OperatorService,
    tenant_id: int = 1,
) -> dict[str, Any]:
    """Processes eBay order notifications, delisting the book everywhere else."""
    event_type = payload.get("metadata", {}).get("topic") or payload.get("type", "order.created")
    logger.info("ebay_webhook.event_received", event_type=event_type, tenant_id=tenant_id)

    skus = EbayService.extract_skus_from_event(payload)
    if not skus:
        logger.info("ebay_webhook.no_skus_found", event_type=event_type)
        return {
            "accepted": True,
            "status": 200,
            "message": "no SKUs detected in event",
            "processed_skus": [],
        }

    processed_skus: list[str] = []
    for sku in skus:
        try:
            proposed = operator.propose(
                operation="sync_inventory",
                payload={
                    "sku": sku,
                    "source_channel": "ebay",
                    "tenant_id": tenant_id,
                    "order_id": str(
                        payload.get("orderId") or payload.get("order_id") or payload.get("notificationId") or ""
                    ),
                },
                tenant_id=tenant_id,
            )
            review_id = proposed["review_id"]
            processed_skus.append(sku)
            logger.info("ebay_webhook.sync_proposed", sku=sku, review_id=review_id, tenant_id=tenant_id)
        except Exception as exc:
            logger.error("ebay_webhook.sync_failed", sku=sku, tenant_id=tenant_id, error=str(exc))

    # Wake Corvus, the Vault Warden, to triage and execute dual delisting
    try:
        from app.agents.corvus import CorvusWarden

        warden = CorvusWarden(operator)
        triage_results = warden.wake_and_triage()
        logger.info("corvus.ebay_triage.complete", **triage_results)
    except Exception as exc:
        logger.error("corvus.ebay_triage.failed", error=str(exc))

    return {"accepted": True, "status": 200, "processed_skus": processed_skus}
