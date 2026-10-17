import pytest

from app.customer_support.email import CustomerResponse, HimalayaMailer
from app.notifications.ntfy import format_sla_alert, send_ntfy
from app.workers.sla_watchdog import SLAWatchdog


def test_unauthenticated_public_topic_is_refused(monkeypatch):
    """Order IDs on a public ntfy topic are readable by anyone who guesses it."""
    monkeypatch.delenv("NTFY_TOKEN", raising=False)
    monkeypatch.delenv("NTFY_SERVER", raising=False)
    with pytest.raises(PermissionError, match="unauthenticated"):
        send_ntfy("orders", "SLA", "late")


def test_opting_in_is_explicit(monkeypatch):
    monkeypatch.delenv("NTFY_TOKEN", raising=False)
    sent = {}

    def fake_urlopen(request, timeout=None):
        sent["url"] = request.full_url
        sent["headers"] = dict(request.headers)
        raise RuntimeError("stop before network")

    monkeypatch.setattr("app.notifications.ntfy.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="stop before network"):
        send_ntfy("orders", "SLA", "late", allow_unauthenticated=True)
    assert sent["url"] == "https://ntfy.sh/orders"
    assert "Authorization" not in sent["headers"]


def test_token_is_sent_and_self_hosted_server_is_honoured(monkeypatch):
    monkeypatch.setenv("NTFY_TOKEN", "tk_secret")
    monkeypatch.setenv("NTFY_SERVER", "https://alerts.internal/")
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.headers.get("Authorization")
        raise RuntimeError("stop")

    monkeypatch.setattr("app.notifications.ntfy.urlopen", fake_urlopen)
    with pytest.raises(RuntimeError):
        send_ntfy("orders", "SLA", "late")
    assert seen["url"] == "https://alerts.internal/orders"
    assert seen["auth"] == "Bearer tk_secret"


@pytest.mark.parametrize("topic", ["", "a/b", "a b"])
def test_topic_must_be_a_single_segment(topic):
    with pytest.raises(ValueError):
        send_ntfy(topic, "t", "m", token="tk")


def test_sla_alert_payload_carries_no_buyer_data():
    from datetime import UTC, datetime, timedelta

    from app.domain.orders import Order

    order = Order("o-1", datetime.now(UTC) - timedelta(hours=23))
    evaluations = SLAWatchdog().audit([order], lambda _: None)
    payload = format_sla_alert(evaluations[0])
    assert "o-1" in payload
    for field in ("recipient", "address", "email", "name"):
        assert field not in payload


def test_himalaya_sends_the_body_over_stdin_not_argv(monkeypatch):
    """Process arguments are world-readable via `ps`; message bodies are not."""
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr("app.customer_support.email.subprocess.run", fake_run)
    response = CustomerResponse("c-1", "buyer@example.test", "Your order", "Tracking: ABC123", approved=True)
    HimalayaMailer(enabled=True).send(response)

    assert "Tracking: ABC123" not in " ".join(captured["argv"])
    assert "Your order" not in " ".join(captured["argv"])
    assert "Tracking: ABC123" in captured["input"]


def test_himalaya_still_refuses_unapproved_or_disabled():
    response = CustomerResponse("c-1", "b@example.test", "s", "b", approved=False)
    with pytest.raises(PermissionError):
        HimalayaMailer(enabled=True).send(response)
    approved = CustomerResponse("c-2", "b@example.test", "s", "b", approved=True)
    with pytest.raises(PermissionError, match="disabled"):
        HimalayaMailer(enabled=False).send(approved)
