"""The agent → review → execute path, end to end.

Every operation the agent layer can submit must have a handler, or an approval
dead-ends and the review queue quietly stops meaning anything.
"""

import pytest

from app.agents.integrations import register_publish_and_support_agents
from app.agents.models import AgentRole, TaskStatus
from app.agents.orchestrator import AgentOrchestrator
from app.customer_support.email import DryRunMailer
from app.domain.audit import AuditLog
from app.publish.layer import DryRunPublisher, PublishGate
from app.review.executor import ApprovedActionExecutor, ExecutionFailed
from app.review.queue import ReviewQueue, ReviewStatus


class Client:
    dry_run = True


def pipeline(*, publish_enabled=False, mailer=None):
    store = ReviewQueue()
    orchestrator = AgentOrchestrator(AuditLog())
    register_publish_and_support_agents(orchestrator, store)
    executor = ApprovedActionExecutor(
        Client(),
        publish_gate=PublishGate(DryRunPublisher(), enabled=publish_enabled),
        mailer=mailer or DryRunMailer(),
    )
    return orchestrator, store, executor


PUBLISH_INPUT = {
    "publish_id": "p-1",
    "resource_type": "product_draft",
    "resource_id": "prod-1",
    "payload": {"title": "Box"},
}
SUPPORT_INPUT = {
    "response_id": "c-1",
    "recipient": "buyer@example.test",
    "subject": "Your order",
    "body": "Your order has shipped.",
    "order_id": "o-1",
}


def run(orchestrator, role, operation, data):
    task = orchestrator.submit(role, operation, data)
    assert orchestrator.run(task.task_id).status is TaskStatus.COMPLETED


def test_every_agent_operation_has_an_executor_handler():
    _, _, executor = pipeline()
    agent_operations = {"publish", "send_customer_response"}
    assert agent_operations <= executor.allowed_operations


def test_publish_reaches_execution_once_publishing_is_enabled():
    orchestrator, store, executor = pipeline(publish_enabled=True)
    run(orchestrator, AgentRole.PUBLISH, "prepare_publish", PUBLISH_INPUT)
    store.decide("p-1", approve=True, reviewer="ada")

    outcome = executor.execute(store, "p-1", actor="grace")
    assert outcome["result"]["dry_run"] is True
    assert outcome["result"]["external_id"] == "dry-run://product_draft/prod-1"
    assert store.get("p-1").status is ReviewStatus.EXECUTED


def test_approval_alone_does_not_publish():
    """Approving a publish review is necessary, not sufficient."""
    orchestrator, store, executor = pipeline(publish_enabled=False)
    run(orchestrator, AgentRole.PUBLISH, "prepare_publish", PUBLISH_INPUT)
    store.decide("p-1", approve=True, reviewer="ada")

    with pytest.raises(PermissionError, match="publishing is disabled"):
        executor.execute(store, "p-1", actor="grace")
    # Nothing was attempted, so the approval survives for when publishing is on.
    assert store.get("p-1").status is ReviewStatus.APPROVED


def test_enabling_publishing_lets_the_same_approval_through():
    orchestrator, store, _ = pipeline()
    run(orchestrator, AgentRole.PUBLISH, "prepare_publish", PUBLISH_INPUT)
    store.decide("p-1", approve=True, reviewer="ada")

    enabled = ApprovedActionExecutor(
        Client(), publish_gate=PublishGate(DryRunPublisher(), enabled=True)
    )
    assert enabled.execute(store, "p-1", actor="grace")["result"]["dry_run"] is True
    assert store.get("p-1").status is ReviewStatus.EXECUTED


def test_disabled_mailer_does_not_burn_the_approval():
    class DisabledMailer:
        enabled = False

        def send(self, response):
            raise AssertionError("must not be reached")

    orchestrator, store, executor = pipeline(mailer=DisabledMailer())
    run(orchestrator, AgentRole.SUPPORT, "draft_customer_response", SUPPORT_INPUT)
    store.decide("c-1", approve=True, reviewer="ada")
    with pytest.raises(PermissionError, match="disabled"):
        executor.execute(store, "c-1", actor="grace")
    assert store.get("c-1").status is ReviewStatus.APPROVED


def test_customer_response_reaches_the_mailer_only_after_approval():
    mailer = DryRunMailer()
    orchestrator, store, executor = pipeline(mailer=mailer)
    run(orchestrator, AgentRole.SUPPORT, "draft_customer_response", SUPPORT_INPUT)

    assert store.get("c-1").status is ReviewStatus.PENDING
    assert mailer.sent == []
    with pytest.raises(PermissionError):
        executor.execute(store, "c-1", actor="grace")

    store.decide("c-1", approve=True, reviewer="ada")
    outcome = executor.execute(store, "c-1", actor="grace")
    assert outcome["result"] == {"delivered": True, "response_id": "c-1"}
    assert [r.recipient for r in mailer.sent] == ["buyer@example.test"]


def test_execution_result_carries_no_buyer_content():
    mailer = DryRunMailer()
    orchestrator, store, executor = pipeline(mailer=mailer)
    run(orchestrator, AgentRole.SUPPORT, "draft_customer_response", SUPPORT_INPUT)
    store.decide("c-1", approve=True, reviewer="ada")
    result = executor.execute(store, "c-1", actor="grace")["result"]
    assert "buyer@example.test" not in str(result)
    assert "Your order has shipped." not in str(result)


def test_rejected_response_is_never_sent():
    mailer = DryRunMailer()
    orchestrator, store, executor = pipeline(mailer=mailer)
    run(orchestrator, AgentRole.SUPPORT, "draft_customer_response", SUPPORT_INPUT)
    store.decide("c-1", approve=False, reviewer="ada", reason="tone")
    with pytest.raises(PermissionError):
        executor.execute(store, "c-1", actor="grace")
    assert mailer.sent == []


def test_malformed_publish_payload_fails_the_item_rather_than_publishing():
    store = ReviewQueue()
    executor = ApprovedActionExecutor(
        Client(), publish_gate=PublishGate(DryRunPublisher(), enabled=True)
    )
    from app.review.queue import ReviewItem

    store.submit(ReviewItem("p-2", "publish", {"publish_id": "p-2"}))
    store.decide("p-2", approve=True, reviewer="ada")
    with pytest.raises(ExecutionFailed):
        executor.execute(store, "p-2", actor="grace")
    assert store.get("p-2").status is ReviewStatus.FAILED
