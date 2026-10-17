from __future__ import annotations

from app.domain.audit import AuditEvent, AuditLog
from app.domain.order_store import OrderStore, reconcile
from app.domain.orders import OrderStatus


def reconcile_orders(store: OrderStore, remote: list[tuple[str, OrderStatus]], audit: AuditLog) -> list[str]:
    drift = reconcile(store.orders.values(), remote)
    audit.record(
        AuditEvent(
            action="orders.reconcile",
            actor="system",
            outcome="drift_detected" if drift else "in_sync",
            detail={"count": str(len(drift))},
        )
    )
    return drift
