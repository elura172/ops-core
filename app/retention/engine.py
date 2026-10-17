from __future__ import annotations

from app.retention.models import CustomerProfile, LifecycleEvent, MessagePlan

TEMPLATES = {
    "order_paid": ("order_paid", False),
    "order_shipped": ("order_shipped", False),
    "order_delivered": ("review_request", True),
    "cart_abandoned": ("cart_reminder", True),
}


class RetentionEngine:
    def __init__(self) -> None:
        self.processed_events: set[str] = set()

    def plan(self, event: LifecycleEvent, profile: CustomerProfile, channel: str = "email") -> MessagePlan | None:
        if event.event_id in self.processed_events:
            return None
        template_info = TEMPLATES.get(event.name)
        if template_info is None:
            return None
        template, marketing = template_info
        if not profile.can_receive(channel, marketing):
            return None
        self.processed_events.add(event.event_id)
        return MessagePlan(
            event.event_id,
            event.customer_id,
            channel,
            template,
            marketing,
            {"order_id": event.order_id or ""},
        )
