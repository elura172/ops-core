from collections.abc import Callable, Iterable

from app.domain.orders import Order, OrderStatus, SLAEvaluation, evaluate_sla

#: The SLA measures time-to-dispatch, so anything already handed to the carrier is
#: out of scope. This also keeps orders whose early events were never delivered from
#: being judged against a created_at we had to estimate from a late event.
OUT_OF_SCOPE = frozenset(
    {
        OrderStatus.SHIPPED,
        OrderStatus.IN_TRANSIT,
        OrderStatus.DELIVERED,
        OrderStatus.CANCELLED,
    }
)


class SLAWatchdog:
    """Evaluate active orders and emit each severity only once per order."""

    def __init__(self, sla_hours: int = 24, warning_ratio: float = 0.70):
        self.sla_hours = sla_hours
        self.warning_ratio = warning_ratio

    def audit(
        self, orders: Iterable[Order], alert: Callable[[SLAEvaluation], None]
    ) -> list[SLAEvaluation]:
        evaluations: list[SLAEvaluation] = []
        for order in orders:
            if order.status in OUT_OF_SCOPE or order.created_at_estimated:
                continue
            evaluation = evaluate_sla(order, self.sla_hours, self.warning_ratio)
            evaluations.append(evaluation)
            if evaluation.severity != "OK" and order.last_alert_severity != evaluation.severity:
                alert(evaluation)
                order.last_alert_severity = evaluation.severity
        return evaluations
