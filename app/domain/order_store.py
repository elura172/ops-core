from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app.domain.orders import Order, OrderStatus


@dataclass(frozen=True)
class OrderEvent:
    event_id: str
    order_id: str
    status: OrderStatus
    occurred_at: datetime


class OrderStore:
    """Small in-memory store for development; replace with Postgres persistence later."""

    def __init__(self) -> None:
        self.orders: dict[str, Order] = {}
        self.processed_event_ids: set[str] = set()

    def apply(self, event: OrderEvent) -> bool:
        """Apply once. Returns False for duplicate events."""
        if not event.event_id or not event.order_id:
            raise ValueError("event_id and order_id are required")
        if event.event_id in self.processed_event_ids:
            return False
        order = self.orders.get(event.order_id)
        if order is None:
            order = Order(event.order_id, event.occurred_at)
            self.orders[event.order_id] = order
        order.transition(event.status)
        self.processed_event_ids.add(event.event_id)
        return True

    def active(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status not in {OrderStatus.DELIVERED, OrderStatus.CANCELLED}]


def reconcile(local: Iterable[Order], remote: Iterable[tuple[str, OrderStatus]]) -> list[str]:
    """Return order IDs whose local status differs from the remote snapshot."""
    local_by_id = {order.order_id: order.status for order in local}
    return [order_id for order_id, status in remote if local_by_id.get(order_id) != status]
