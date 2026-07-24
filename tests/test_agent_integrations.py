from app.agents.integrations import register_publish_and_support_agents
from app.agents.models import AgentRole, TaskStatus
from app.agents.orchestrator import AgentOrchestrator
from app.domain.audit import AuditLog
from app.review.queue import ReviewQueue


def test_publish_and_support_agents_only_create_reviews():
    reviews = ReviewQueue()
    orchestrator = AgentOrchestrator(AuditLog())
    register_publish_and_support_agents(orchestrator, reviews)
    publish = orchestrator.submit(
        AgentRole.PUBLISH,
        "prepare_publish",
        {
            "publish_id": "p-1",
            "resource_type": "product_draft",
            "resource_id": "prod-1",
            "payload": {"status": "DRAFT"},
        },
    )
    support = orchestrator.submit(
        AgentRole.SUPPORT,
        "draft_customer_response",
        {
            "response_id": "r-1",
            "recipient": "buyer@example.test",
            "subject": "Update",
            "body": "Your order is being processed.",
        },
    )
    assert orchestrator.run(publish.task_id).status == TaskStatus.COMPLETED
    assert orchestrator.run(support.task_id).status == TaskStatus.COMPLETED
    assert {item.review_id for item in reviews.pending()} == {"p-1", "r-1"}
