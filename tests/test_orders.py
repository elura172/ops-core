from datetime import UTC, datetime, timedelta

import pytest

from app.domain.orders import Order, OrderStatus, evaluate_sla
from app.workers.sla_watchdog import SLAWatchdog


def test_order_state_machine_rejects_skips():
    order = Order("o-1", datetime.now(UTC))
    with pytest.raises(ValueError):
        order.transition(OrderStatus.SHIPPED)


def test_watchdog_alerts_once_per_severity():
    order = Order("o-2", datetime.now(UTC) - timedelta(hours=22))
    alerts = []
    watchdog = SLAWatchdog()
    watchdog.audit([order], alerts.append)
    watchdog.audit([order], alerts.append)
    assert len(alerts) == 1
    assert alerts[0].severity == "CRITICAL"


def test_sla_future_order_is_safe():
    order = Order("o-3", datetime.now(UTC) + timedelta(hours=1))
    assert evaluate_sla(order).severity == "OK"
