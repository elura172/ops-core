import pytest

from app.customer_support.email import CustomerResponse, DryRunMailer, HimalayaMailer
from app.publish.layer import DryRunPublisher, PublishGate, PublishRequest


def request():
    return PublishRequest("pub-1", "product_draft", "p-1", {"status": "DRAFT"})


def test_publish_requires_approval_and_enablement():
    gate = PublishGate(DryRunPublisher(), enabled=False)
    with pytest.raises(PermissionError):
        gate.execute(request(), approved=False)
    with pytest.raises(PermissionError):
        gate.execute(request(), approved=True)
    result = PublishGate(DryRunPublisher(), enabled=True).execute(request(), approved=True)
    assert result.dry_run


def test_email_dry_run_requires_approval_and_blocks_credentials():
    mailer = DryRunMailer()
    response = CustomerResponse("r-1", "buyer@example.test", "Order update", "Your order is being processed.", approved=True)
    mailer.send(response)
    assert len(mailer.sent) == 1
    with pytest.raises(ValueError):
        CustomerResponse("r-2", "buyer@example.test", "Help", "Please send your credit card number", approved=True).validate()


def test_himalaya_is_disabled_by_default():
    response = CustomerResponse("r-3", "buyer@example.test", "Update", "Hello", approved=True)
    with pytest.raises(PermissionError):
        HimalayaMailer().send(response)
