import hashlib
import hmac
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.http import create_app
from app.auth import create_access_token
from app.db.users import SQLiteUserStore
from tests.conftest import auth

WEBHOOK_SECRET = "whsec_test_billing_secret"


class FakeStripeClient:
    def __init__(self, _secret_key):
        self.checkout_params = None
        self.portal_params = None
        self.subscriptions = {}
        self.fail_retrieve = False
        self.v1 = SimpleNamespace(
            checkout=SimpleNamespace(sessions=SimpleNamespace(create=self.create_checkout_session)),
            billing_portal=SimpleNamespace(
                sessions=SimpleNamespace(create=self.create_portal_session),
            ),
            subscriptions=SimpleNamespace(retrieve=self.retrieve_subscription),
        )

    def create_checkout_session(self, *, params):
        self.checkout_params = params
        return SimpleNamespace(url="https://checkout.test/session")

    def create_portal_session(self, *, params):
        self.portal_params = params
        return SimpleNamespace(url="https://billing.test/session")

    def retrieve_subscription(self, subscription_id):
        if self.fail_retrieve:
            raise RuntimeError("Stripe API unavailable")
        return self.subscriptions[subscription_id]


@pytest.fixture
def billing_env(settings, monkeypatch):
    stripe_client = FakeStripeClient("sk_test_unit")
    monkeypatch.setattr("stripe.StripeClient", lambda key: stripe_client)
    user_store = SQLiteUserStore(":memory:")
    test_settings = replace(
        settings,
        stripe_secret_key="sk_test_unit",
        stripe_webhook_secret=WEBHOOK_SECRET,
        stripe_price_id="price_test_monthly",
    )
    client = TestClient(
        create_app(settings=test_settings, user_store=user_store),
        base_url="https://ops-core.test",
    )
    try:
        yield client, user_store, stripe_client
    finally:
        user_store.close()


def post_event(client, event_type, obj):
    event = {
        "id": "evt_test_billing",
        "object": "event",
        "api_version": "2024-12-18.acacia",
        "created": int(time.time()),
        "data": {"object": obj},
        "livemode": False,
        "pending_webhooks": 1,
        "type": event_type,
    }
    payload = json.dumps(event, separators=(",", ":")).encode()
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.".encode() + payload
    signature = hmac.new(WEBHOOK_SECRET.encode(), signed_payload, hashlib.sha256).hexdigest()
    return client.post(
        "/billing/webhook",
        content=payload,
        headers={"Stripe-Signature": f"t={timestamp},v1={signature}"},
    )


def test_checkout_uses_dynamic_payment_methods_and_a_per_app_stripe_client(settings, monkeypatch):
    stripe_client = FakeStripeClient("sk_test_unit")
    monkeypatch.setattr("stripe.StripeClient", lambda key: stripe_client)
    user_store = SQLiteUserStore(":memory:")
    test_settings = replace(
        settings,
        stripe_secret_key="sk_test_unit",
        stripe_price_id="price_test_monthly",
    )
    client = TestClient(
        create_app(settings=test_settings, user_store=user_store),
        base_url="https://ops-core.test",
    )
    tenant_id = user_store.create_tenant("Checkout tenant")
    token = create_access_token({"sub": "buyer@example.test", "tenant_id": tenant_id, "role": "admin"})

    response = client.post(
        "/billing/checkout",
        json={
            "success_url": "https://ops-core.test/success",
            "cancel_url": "https://ops-core.test/cancel",
        },
        headers=auth(token),
    )

    assert response.status_code == 200
    assert response.json()["checkout_url"] == "https://checkout.test/session"
    assert stripe_client.checkout_params["mode"] == "subscription"
    assert "payment_method_types" not in stripe_client.checkout_params
    assert stripe_client.checkout_params["line_items"] == [{"price": "price_test_monthly", "quantity": 1}]
    assert "price_data" not in stripe_client.checkout_params["line_items"][0]
    assert stripe_client.checkout_params["client_reference_id"] == str(tenant_id)
    user_store.close()


def test_checkout_rejects_external_return_urls(billing_env):
    client, user_store, _stripe_client = billing_env
    tenant_id = user_store.create_tenant("Redirect validation tenant")
    token = create_access_token({"sub": "buyer@example.test", "tenant_id": tenant_id, "role": "admin"})

    response = client.post(
        "/billing/checkout",
        json={
            "success_url": "https://attacker.test/landing",
            "cancel_url": "https://ops-core.test/cancel",
        },
        headers={**auth(token), "Origin": "https://attacker.test"},
    )

    assert response.status_code == 400


def test_customer_portal_is_scoped_to_the_authenticated_tenant(billing_env):
    client, user_store, stripe_client = billing_env
    tenant_id = user_store.create_tenant("Portal tenant")
    user_store.update_tenant_stripe_customer(tenant_id, "cus_portal")
    token = create_access_token({"sub": "buyer@example.test", "tenant_id": tenant_id, "role": "admin"})

    response = client.post("/billing/portal", headers=auth(token))

    assert response.status_code == 200
    assert response.json()["portal_url"] == "https://billing.test/session"
    assert stripe_client.portal_params == {
        "customer": "cus_portal",
        "return_url": "https://ops-core.test/dashboard",
    }


def test_customer_portal_requires_a_linked_stripe_customer(billing_env):
    client, user_store, _stripe_client = billing_env
    tenant_id = user_store.create_tenant("Unlinked portal tenant")
    token = create_access_token({"sub": "buyer@example.test", "tenant_id": tenant_id, "role": "admin"})

    response = client.post("/billing/portal", headers=auth(token))

    assert response.status_code == 409


def test_checkout_stays_unavailable_without_a_stripe_price_id(settings, monkeypatch):
    stripe_client = FakeStripeClient("sk_test_unit")
    monkeypatch.setattr("stripe.StripeClient", lambda key: stripe_client)
    user_store = SQLiteUserStore(":memory:")
    test_settings = replace(settings, stripe_secret_key="sk_test_unit")
    client = TestClient(
        create_app(settings=test_settings, user_store=user_store),
        base_url="https://ops-core.test",
    )
    tenant_id = user_store.create_tenant("Missing price tenant")
    token = create_access_token({"sub": "buyer@example.test", "tenant_id": tenant_id, "role": "admin"})

    response = client.post(
        "/billing/checkout",
        json={
            "success_url": "https://ops-core.test/success",
            "cancel_url": "https://ops-core.test/cancel",
        },
        headers=auth(token),
    )

    assert response.status_code == 503
    assert stripe_client.checkout_params is None
    user_store.close()


def test_unpaid_checkout_does_not_grant_access_but_async_paid_trial_does(
    billing_env,
):
    client, user_store, stripe_client = billing_env
    tenant_id = user_store.create_tenant("Async payment tenant")
    checkout_session = {
        "id": "cs_test_1",
        "object": "checkout.session",
        "client_reference_id": str(tenant_id),
        "customer": "cus_test_1",
        "payment_status": "unpaid",
        "subscription": "sub_test_1",
    }

    completed = post_event(client, "checkout.session.completed", checkout_session)

    assert completed.status_code == 200
    assert user_store.get_tenant_billing_status(tenant_id) == {
        "stripe_customer_id": "cus_test_1",
        "subscription_status": "inactive",
    }

    stripe_client.subscriptions["sub_test_1"] = {
        "id": "sub_test_1",
        "customer": "cus_test_1",
        "status": "trialing",
    }
    checkout_session["payment_status"] = "paid"
    paid = post_event(client, "checkout.session.async_payment_succeeded", checkout_session)

    assert paid.status_code == 200
    assert user_store.get_tenant_billing_status(tenant_id)["subscription_status"] == "active"


def test_subscription_and_invoice_events_use_current_stripe_state(billing_env):
    client, user_store, stripe_client = billing_env
    tenant_id = user_store.create_tenant("Lifecycle tenant")
    user_store.update_tenant_stripe_customer(tenant_id, "cus_lifecycle")
    stripe_client.subscriptions["sub_lifecycle"] = {
        "id": "sub_lifecycle",
        "customer": "cus_lifecycle",
        "status": "active",
    }

    # The event snapshot is stale; Stripe's current subscription remains active.
    updated = post_event(
        client,
        "customer.subscription.updated",
        {
            "id": "sub_lifecycle",
            "customer": "cus_lifecycle",
            "status": "past_due",
        },
    )
    assert updated.status_code == 200
    assert user_store.get_tenant_billing_status(tenant_id)["subscription_status"] == "active"

    # A paid invoice does not grant access when the subscription itself is past due.
    stripe_client.subscriptions["sub_lifecycle"]["status"] = "past_due"
    paid_invoice = post_event(
        client,
        "invoice.paid",
        {"id": "in_test_1", "subscription": "sub_lifecycle"},
    )
    assert paid_invoice.status_code == 200
    assert user_store.get_tenant_billing_status(tenant_id)["subscription_status"] == "inactive"


def test_current_subscription_lookup_failure_retries_webhook(billing_env, caplog):
    client, user_store, stripe_client = billing_env
    tenant_id = user_store.create_tenant("Retry tenant")
    user_store.update_tenant_stripe_customer(tenant_id, "cus_retry")
    stripe_client.fail_retrieve = True

    response = post_event(
        client,
        "customer.subscription.updated",
        {"id": "sub_retry", "customer": "cus_retry", "status": "active"},
    )

    assert response.status_code == 503
    assert "error_type=RuntimeError" in caplog.text
    assert "Stripe API unavailable" not in caplog.text
    assert user_store.get_tenant_billing_status(tenant_id)["subscription_status"] == "inactive"


def test_stripe_webhook_rejects_invalid_signature(billing_env):
    client, _user_store, _stripe_client = billing_env
    response = client.post(
        "/billing/webhook",
        content=b'{"type":"invoice.paid"}',
        headers={"Stripe-Signature": "t=1,v1=invalid"},
    )
    assert response.status_code == 400


def test_billing_routers_keep_each_app_bound_to_its_own_store_and_stripe_client(settings, monkeypatch):
    stripe_clients = {
        "sk_test_first": FakeStripeClient("sk_test_first"),
        "sk_test_second": FakeStripeClient("sk_test_second"),
    }
    monkeypatch.setattr("stripe.StripeClient", lambda key: stripe_clients[key])
    first_store = SQLiteUserStore(":memory:")
    second_store = SQLiteUserStore(":memory:")
    first_tenant = first_store.create_tenant("First app tenant")
    second_tenant = second_store.create_tenant("Second app tenant")
    first_store.update_tenant_stripe_customer(first_tenant, "cus_first")
    second_store.update_tenant_stripe_customer(second_tenant, "cus_second")
    stripe_clients["sk_test_first"].subscriptions["sub_first"] = {
        "id": "sub_first",
        "customer": "cus_first",
        "status": "active",
    }
    stripe_clients["sk_test_second"].subscriptions["sub_second"] = {
        "id": "sub_second",
        "customer": "cus_second",
        "status": "active",
    }
    first_app = create_app(
        settings=replace(
            settings,
            stripe_secret_key="sk_test_first",
            stripe_webhook_secret=WEBHOOK_SECRET,
        ),
        user_store=first_store,
    )
    second_app = create_app(
        settings=replace(
            settings,
            stripe_secret_key="sk_test_second",
            stripe_webhook_secret=WEBHOOK_SECRET,
        ),
        user_store=second_store,
    )

    second_response = post_event(
        TestClient(second_app),
        "customer.subscription.updated",
        {"id": "sub_second", "customer": "cus_second", "status": "active"},
    )

    assert second_response.status_code == 200
    assert second_store.get_tenant_billing_status(second_tenant)["subscription_status"] == "active"
    assert first_store.get_tenant_billing_status(first_tenant)["subscription_status"] == "inactive"

    first_response = post_event(
        TestClient(first_app),
        "customer.subscription.updated",
        {"id": "sub_first", "customer": "cus_first", "status": "active"},
    )
    assert first_response.status_code == 200
    assert first_store.get_tenant_billing_status(first_tenant)["subscription_status"] == "active"
    first_store.close()
    second_store.close()
