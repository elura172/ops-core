from __future__ import annotations

from app.agents.models import AgentRole
from app.agents.orchestrator import AgentOrchestrator
from app.customer_support.email import CustomerResponse
from app.publish.layer import PublishRequest
from app.review.queue import ReviewItem, ReviewQueue


def register_publish_and_support_agents(orchestrator: AgentOrchestrator, reviews: ReviewQueue) -> None:
    """Register draft-only handlers; outputs become human-review items."""

    def prepare_publish(data: dict) -> dict:
        request = PublishRequest(**data)
        reviews.submit(ReviewItem(request.publish_id, "publish", {"resource_type": request.resource_type, "resource_id": request.resource_id, "payload": request.payload}))
        return {"review_id": request.publish_id, "status": "PENDING"}

    def draft_customer_response(data: dict) -> dict:
        response = CustomerResponse(**data)
        response.validate()
        reviews.submit(ReviewItem(response.response_id, "send_customer_response", {"recipient": response.recipient, "subject": response.subject, "body": response.body, "order_id": response.order_id}))
        return {"review_id": response.response_id, "status": "PENDING"}

    orchestrator.register(AgentRole.PUBLISH, "prepare_publish", prepare_publish)
    orchestrator.register(AgentRole.SUPPORT, "draft_customer_response", draft_customer_response)
