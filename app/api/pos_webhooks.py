import base64
import hashlib
import hmac
import json
from typing import Any

from app.api.operator import OperatorService
from app.observability.logging import get_logger

logger = get_logger("pos_webhook")

def verify_square_signature(url: str, body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    # Square signature is HMAC-SHA256 of the URL + body, base64 encoded
    content = url.encode("utf-8") + body
    expected = hmac.new(secret.encode("utf-8"), content, hashlib.sha256).digest()
    expected_b64 = base64.b64encode(expected).decode("utf-8")
    return hmac.compare_digest(expected_b64, signature)

def handle_square_webhook(
    url: str,
    body: bytes,
    signature: str,
    secret: str,
    operator: OperatorService,
    tenant_id: int = 1,
) -> dict[str, Any]:
    if not verify_square_signature(url, body, signature, secret):
        logger.warning("square_webhook.signature_rejected", byte_length=len(body))
        return {"accepted": False, "error": "invalid signature", "status": 401}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning("square_webhook.payload_rejected", reason=str(exc))
        return {"accepted": False, "error": "invalid json", "status": 400}

    event_type = payload.get("type")
    
    # We only care about order creation/payment completion which implies a sale
    if event_type not in ("order.created", "order.updated"):
        return {"accepted": True, "status": 200, "message": f"ignored event type {event_type}"}
        
    order = payload.get("data", {}).get("object", {}).get("order", {})
    state = order.get("state", "")
    
    if state not in ("COMPLETED", "OPEN"):
        return {"accepted": True, "status": 200, "message": f"ignored order state {state}"}

    line_items = order.get("line_items", [])
    processed_skus = []
    
    for item in line_items:
        # In Square, the SKU is often tracked under note, sku, or catalog_object_id
        raw_sku = item.get("note", "").strip() or item.get("sku", "").strip() or item.get("catalog_object_id", "")
        sku = raw_sku
        for prefix in ("sq-cat-", "#item-", "#var-"):
            if sku.startswith(prefix):
                sku = sku.removeprefix(prefix)
                break
        
        if not sku:
            continue
            
        try:
            # 1. Propose
            proposed = operator.propose(
                operation="sync_inventory",
                payload={"sku": sku, "source_channel": "square", "tenant_id": tenant_id},
                tenant_id=tenant_id,
            )
            review_id = proposed["review_id"]
            processed_skus.append(sku)
            logger.info("square_webhook.sync_proposed", sku=sku, review_id=review_id, tenant_id=tenant_id)
            
        except Exception as exc:
            logger.error("square_webhook.sync_failed", sku=sku, tenant_id=tenant_id, error=str(exc))
            # Continue processing other items even if one fails

    # Wake Corvus, the Vault Warden, to triage the newly populated queue
    try:
        from app.agents.corvus import CorvusWarden
        warden = CorvusWarden(operator)
        triage_results = warden.wake_and_triage()
        logger.info("corvus.triage.complete", **triage_results)
    except Exception as exc:
        logger.error("corvus.triage.failed", error=str(exc))

    return {"accepted": True, "status": 200, "processed_skus": processed_skus}
