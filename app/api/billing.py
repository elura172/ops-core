from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import stripe
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import Settings
from app.db.users import UserStore


class CheckoutRequest(BaseModel):
    success_url: str
    cancel_url: str


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

    def refresh_subscription_status(subscription_id: str) -> None:
        if stripe_client is None:
            raise HTTPException(503, "Stripe is not configured")
        try:
            subscription = stripe_client.v1.subscriptions.retrieve(subscription_id)
        except Exception as exc:
            # Return a retryable response without exposing Stripe request details.
            raise HTTPException(503, "Unable to confirm subscription status") from exc

        customer_id = _object_id(_value(subscription, "customer"))
        if customer_id:
            status = _value(subscription, "status")
            user_store.update_subscription_status(
                customer_id,
                _mapped_subscription_status(status),
            )

    def link_checkout_customer(session: Any) -> None:
        tenant_id = _value(session, "client_reference_id")
        customer_id = _object_id(_value(session, "customer"))
        if not customer_id or tenant_id is None:
            return
        try:
            tenant_id_int = int(tenant_id)
        except (TypeError, ValueError):
            return
        if tenant_id_int > 0:
            user_store.update_tenant_stripe_customer(tenant_id_int, customer_id)

    @router.post("/checkout")
    def create_checkout_session(req: CheckoutRequest, request: Request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(401, "Missing token")

        token = auth_header.split(" ", 1)[1]
        from app.auth import decode_access_token

        try:
            payload = decode_access_token(token)
            tenant_id = int(payload["tenant_id"])
            email = payload["sub"]
        except Exception as exc:
            raise HTTPException(401, "Invalid token") from exc

        billing = user_store.get_tenant_billing_status(tenant_id)
        if billing.get("subscription_status") in {"active", "trialing"}:
            raise HTTPException(400, "Already subscribed")
        _validate_return_urls(req, request)
        if stripe_client is None:
            raise HTTPException(503, "Stripe is not configured")
        if not settings.stripe_price_id:
            raise HTTPException(503, "Stripe subscription price is not configured")

        try:
            session = stripe_client.v1.checkout.sessions.create(
                params={
                    "line_items": [
                        {
                            "price": settings.stripe_price_id,
                            "quantity": 1,
                        }
                    ],
                    "mode": "subscription",
                    "success_url": req.success_url,
                    "cancel_url": req.cancel_url,
                    "customer_email": email,
                    "client_reference_id": str(tenant_id),
                }
            )
            return {"checkout_url": session.url}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, "Unable to create Stripe checkout session") from exc

    @router.post("/webhook")
    async def stripe_webhook(
        request: Request,
        stripe_signature: str | None = Header(default=None),
    ):
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

        event_type = _value(event, "type")
        event_data = _value(event, "data")
        resource = _value(event_data, "object") if event_data is not None else None

        if event_type in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
            "checkout.session.async_payment_failed",
        }:
            link_checkout_customer(resource)
            payment_status = _value(resource, "payment_status")
            subscription_id = _object_id(_value(resource, "subscription"))
            if (
                subscription_id
                and event_type != "checkout.session.async_payment_failed"
                and payment_status in {"paid", "no_payment_required"}
            ) or (subscription_id and event_type == "checkout.session.async_payment_failed"):
                refresh_subscription_status(subscription_id)

        elif event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
            "customer.subscription.paused",
            "customer.subscription.resumed",
        }:
            subscription_id = _object_id(_value(resource, "id"))
            if subscription_id:
                refresh_subscription_status(subscription_id)

        elif event_type in {"invoice.paid", "invoice.payment_failed"}:
            subscription_id = _invoice_subscription_id(resource)
            if subscription_id:
                # An invoice event alone does not prove the subscription is active;
                # read Stripe's current subscription state before changing access.
                refresh_subscription_status(subscription_id)

        return {"status": "success"}

    return router
