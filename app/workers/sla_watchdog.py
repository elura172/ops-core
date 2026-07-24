from collections.abc import Callable, Iterable

from app.domain.orders import Order, SLAEvaluation, OrderStatus, evaluate_sla


class SLAWatchdog:
    """Evaluate active orders and emit each severity only once per order."""

    def __init__(self, sla_hours: int = 24, warning_ratio: float = 0.70):
        self.sla_hours = sla_hours
        self.warning_ratio = warning_ratio

    def audit(self, orders: Iterable[Order], alert: Callable[[SLAEvaluation], None]) -> list[SLAEvaluation]:
        evaluations: list[SLAEvaluation] = []
        terminal = {OrderStatus.DELIVERED, OrderStatus.CANCELLED}
        for order in orders:
            if order.status in terminal:
                continue
            evaluation = evaluate_sla(order, self.sla_hours, self.warning_ratio)
            evaluations.append(evaluation)
            if evaluation.severity != "OK" and order.last_alert_severity != evaluation.severity:
                alert(evaluation)
                order.last_alert_severity = evaluation.severity
        return evaluations
