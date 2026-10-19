import logging
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import urlsplit

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import Settings
from app.db.users import UserStore

logger = logging.getLogger(__name__)


class CheckoutRequest(BaseModel):
    success_url: str
    cancel_url: str


class CheckoutVerifyRequest(BaseModel):
    session_id: str


def _value(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    getter = getattr(obj, "get", None)
    if getter:
        return getter(key)
    return getattr(obj, key, None)


def _object_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    object_id = _value(value, "id") if value is not None else None
    return object_id if isinstance(object_id, str) else None


def _invoice_subscription_id(invoice: Any) -> str | None:
    """Read the subscription from both legacy and current Invoice shapes."""
    subscription_id = _object_id(_value(invoice, "subscription"))
    if subscription_id:
        return subscription_id

    parent = _value(invoice, "parent")
    details = _value(parent, "subscription_details") if parent is not None else None
    return _object_id(_value(details, "subscription")) if details is not None else None


def _mapped_subscription_status(status: str | None) -> str:
    # Stripe trials grant product access. Payment-problem and terminal states do not.
    return "active" if status in {"active", "trialing"} else "inactive"


def _validate_return_urls(req: CheckoutRequest, request: Request) -> None:
    origin = str(request.base_url)
    try:
        expected = urlsplit(origin)
        targets = (urlsplit(req.success_url), urlsplit(req.cancel_url))
    except ValueError as exc:
        raise HTTPException(400, "Invalid checkout return URL") from exc
    for target in targets:
        if (
            expected.scheme not in {"http", "https"}
            or not expected.netloc
            or target.scheme != expected.scheme
            or target.netloc.casefold() != expected.netloc.casefold()
            or target.username is not None
            or target.password is not None
        ):
            raise HTTPException(400, "Checkout return URLs must use this app's origin")


def get_billing_router(settings: Settings, user_store: UserStore) -> APIRouter:
    # Routers are app-scoped: create_app() can be called repeatedly in tests or
    # workers without reusing routes that close over another store/settings pair.
    router = APIRouter()
    stripe_client = stripe.StripeClient(settings.stripe_secret_key) if settings.stripe_secret_key else None

    def refresh_subscription_status(subscription_id: str) -> tuple[str, str | None]:
        if stripe_client is None:
            raise HTTPException(503, "Stripe is not configured")
        try:
            subscription = stripe_client.v1.subscriptions.retrieve(subscription_id)
        except Exception as exc:
            # Never log the exception message: authentication errors can include
            # fragments of the supplied credential.
            status = getattr(exc, "http_status", None)
            logger.warning(
                "Stripe subscription lookup failed: error_type=%s http_status=%s",
                type(exc).__name__,
                status if isinstance(status, int) else None,
            )
            # Return a retryable response without exposing Stripe request details.
            raise HTTPException(503, "Unable to confirm subscription status") from exc

        customer_id = _object_id(_value(subscription, "customer"))
        raw_status = _value(subscription, "status")
        raw_status_str = str(raw_status) if isinstance(raw_status, str) else None
        mapped_status = _mapped_subscription_status(raw_status_str)
        if customer_id:
            user_store.update_subscription_status(
                customer_id,
                mapped_status,
                raw_status_str,
            )
        return mapped_status, raw_status_str

    def link_checkout_customer(session: Any) -> int | None:
        tenant_id = _value(session, "client_reference_id")
        customer_id = _object_id(_value(session, "customer"))
        if tenant_id is None:
            return None
        try:
            tenant_id_int = int(tenant_id)
        except (TypeError, ValueError):
            return None
        if tenant_id_int > 0 and customer_id:
            user_store.update_tenant_stripe_customer(tenant_id_int, customer_id)
        return tenant_id_int if tenant_id_int > 0 else None

    def tenant_from_request(request: Request) -> tuple[int, str]:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(401, "Missing token")

        from app.auth import decode_access_token

        try:
            payload = decode_access_token(auth_header.split(" ", 1)[1])
            return int(payload["tenant_id"]), str(payload["sub"])
        except Exception as exc:
            raise HTTPException(401, "Invalid token") from exc

    def _create_portal_url(customer_id: str, request: Request) -> str:
        if stripe_client is None:
            raise HTTPException(503, "Stripe is not configured")
        try:
            session = stripe_client.v1.billing_portal.sessions.create(
                params={
                    "customer": customer_id,
                    "return_url": f"{str(request.base_url).rstrip('/')}/dashboard",
                }
            )
            return str(session.url)
        except Exception as exc:
            raise HTTPException(502, "Unable to open subscription management") from exc

    def _find_existing_customer_subscription(customer_id: str) -> tuple[str | None, str | None]:
        """Inspect live Stripe subscriptions for an existing customer before creating Checkout."""
        if stripe_client is None:
            return None, None
        list_fn = getattr(stripe_client.v1.subscriptions, "list", None)
        if not callable(list_fn):
            return None, None
        try:
            result = list_fn(params={"customer": customer_id, "status": "all", "limit": 10})
            items = _value(result, "data") or []
        except Exception as exc:
            status = getattr(exc, "http_status", None)
            logger.warning(
                "Stripe customer subscription list failed: error_type=%s http_status=%s",
                type(exc).__name__,
                status if isinstance(status, int) else None,
            )
            return None, None

        for preferred in ("active", "trialing", "past_due", "unpaid"):
            for sub in items:
                sub_status = _value(sub, "status")
                sub_id = _object_id(_value(sub, "id"))
                if sub_status == preferred and sub_id:
                    return sub_id, str(sub_status)
        return None, None

    @router.post("/checkout")
    def create_checkout_session(req: CheckoutRequest, request: Request) -> dict[str, Any]:
        tenant_id, email = tenant_from_request(request)

        billing = user_store.get_tenant_billing_status(tenant_id)
        if billing.get("subscription_status") in {"active", "trialing"}:
            raise HTTPException(400, "Already subscribed")
        _validate_return_urls(req, request)
        if stripe_client is None:
            raise HTTPException(503, "Stripe is not configured")
        if not settings.stripe_price_id:
            raise HTTPException(503, "Stripe subscription price is not configured")

        existing_customer_id = billing.get("stripe_customer_id")
        if existing_customer_id:
            sub_id, live_status = _find_existing_customer_subscription(existing_customer_id)
            if live_status in {"active", "trialing"}:
                user_store.update_subscription_status(
                    existing_customer_id,
                    "active",
                    live_status,
                )
                return {
                    "already_active": True,
                    "subscription_status": "active",
                    "stripe_subscription_status": live_status,
                }
            if live_status in {"past_due", "unpaid"}:
                user_store.update_subscription_status(
                    existing_customer_id,
                    "inactive",
                    live_status,
                )
                portal_url = _create_portal_url(existing_customer_id, request)
                return {
                    "portal_url": portal_url,
                    "subscription_status": "inactive",
                    "stripe_subscription_status": live_status,
                }

        checkout_params: dict[str, Any] = {
            "line_items": [
                {
                    "price": settings.stripe_price_id,
                    "quantity": 1,
                }
            ],
            "mode": "subscription",
            "success_url": req.success_url,
            "cancel_url": req.cancel_url,
            "client_reference_id": str(tenant_id),
        }
        if existing_customer_id:
            checkout_params["customer"] = existing_customer_id
        else:
            checkout_params["customer_email"] = email

        try:
            session = stripe_client.v1.checkout.sessions.create(params=cast(Any, checkout_params))
            return {"checkout_url": session.url}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, "Unable to create Stripe checkout session") from exc

    @router.post("/verify-session")
    def verify_checkout_session(req: CheckoutVerifyRequest, request: Request) -> dict[str, Any]:
        tenant_id, _email = tenant_from_request(request)
        session_id = req.session_id.strip()
        if not session_id or not session_id.startswith("cs_"):
            raise HTTPException(400, "Invalid checkout session ID")
        if stripe_client is None:
            raise HTTPException(503, "Stripe is not configured")

        retrieve_fn = getattr(stripe_client.v1.checkout.sessions, "retrieve", None)
        if not callable(retrieve_fn):
            raise HTTPException(503, "Stripe checkout verification is unavailable")

        try:
            session = retrieve_fn(session_id)
        except Exception as exc:
            status = getattr(exc, "http_status", None)
            logger.warning(
                "Stripe checkout session lookup failed: error_type=%s http_status=%s",
                type(exc).__name__,
                status if isinstance(status, int) else None,
            )
            raise HTTPException(502, "Unable to verify checkout session") from exc

        ref_tenant = _value(session, "client_reference_id")
        try:
            ref_tenant_int = int(ref_tenant) if ref_tenant is not None else None
        except (TypeError, ValueError):
            ref_tenant_int = None

        if ref_tenant_int != tenant_id:
            raise HTTPException(403, "Checkout session does not belong to this workspace")

        link_checkout_customer(session)
        payment_status = _value(session, "payment_status")
        subscription_id = _object_id(_value(session, "subscription"))
        if subscription_id and payment_status in {"paid", "no_payment_required"}:
            refresh_subscription_status(subscription_id)

        billing = user_store.get_tenant_billing_status(tenant_id)
        return {
            "subscription_status": billing.get("subscription_status", "inactive"),
            "stripe_subscription_status": billing.get("stripe_subscription_status"),
            "has_billing_account": bool(billing.get("stripe_customer_id")),
        }

    @router.post("/portal")
    def create_customer_portal_session(request: Request) -> dict[str, Any]:
        tenant_id, _email = tenant_from_request(request)
        billing = user_store.get_tenant_billing_status(tenant_id)
        customer_id = billing.get("stripe_customer_id")
        if not customer_id:
            raise HTTPException(409, "No billing account is linked to this workspace")
        portal_url = _create_portal_url(customer_id, request)
        return {"portal_url": portal_url}

    @router.post("/webhook")
    async def stripe_webhook(
        request: Request,
        stripe_signature: str | None = Header(default=None),
    ) -> dict[str, Any]:
        webhook_secret = settings.stripe_webhook_secret
        if not webhook_secret:
            raise HTTPException(503, "Stripe webhook is not configured")

        payload = await request.body()
        try:
            event = stripe.Webhook.construct_event(payload, stripe_signature, webhook_secret)
        except ValueError as exc:
            raise HTTPException(400, "Invalid payload") from exc
        except stripe.error.SignatureVerificationError as exc:
            raise HTTPException(400, "Invalid signature") from exc

        event_id = _object_id(_value(event, "id"))
        event_type = _value(event, "type")
        event_type_str = str(event_type) if isinstance(event_type, str) else "unknown"

        if (
            event_id
            and hasattr(user_store, "has_processed_webhook_event")
            and user_store.has_processed_webhook_event(event_id)
        ):
            return {"status": "duplicate"}

        event_data = _value(event, "data")
        resource = _value(event_data, "object") if event_data is not None else None

        if event_type_str in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
            "checkout.session.async_payment_failed",
        }:
            link_checkout_customer(resource)
            payment_status = _value(resource, "payment_status")
            subscription_id = _object_id(_value(resource, "subscription"))
            if (
                subscription_id
                and event_type_str != "checkout.session.async_payment_failed"
                and payment_status in {"paid", "no_payment_required"}
            ) or (subscription_id and event_type_str == "checkout.session.async_payment_failed"):
                refresh_subscription_status(subscription_id)

        elif event_type_str in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "customer.subscription.paused",
            "customer.subscription.resumed",
        }:
            subscription_id = _object_id(_value(resource, "id"))
            if subscription_id:
                refresh_subscription_status(subscription_id)

        elif event_type_str in {"invoice.paid", "invoice.payment_failed"}:
            subscription_id = _invoice_subscription_id(resource)
            if subscription_id:
                # An invoice event alone does not prove the subscription is active;
                # read Stripe's current subscription state before changing access.
                refresh_subscription_status(subscription_id)

        if event_id and hasattr(user_store, "record_webhook_event"):
            user_store.record_webhook_event(event_id, event_type_str)

        return {"status": "success"}

    return router
