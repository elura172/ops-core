"""Rising-edge alerts evaluated against the in-process metrics registry.

No alerting infrastructure is assumed: rules are evaluated in-process (on
``/alerts`` reads and after executions) and firings are structured log events
plus an inspectable history. A deployment that grows a Prometheus server can
lift the same conditions into ``observability/prometheus-alerts.yml`` and
retire this evaluator without touching the instrumented code.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.observability import metrics
from app.observability.logging import get_logger

logger = get_logger("alerts")


@dataclass(frozen=True)
class AlertRule:
    name: str
    severity: str
    message: str
    #: Current value of the watched quantity; the rule fires when it rises.
    value: Callable[[], float]


@dataclass(frozen=True)
class Firing:
    rule: str
    severity: str
    message: str
    value: float
    fired_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def default_rules() -> list[AlertRule]:
    return [
        AlertRule(
            name="ReviewExecutionFailed",
            severity="page",
            message=("an approved mutation failed mid-execution; the item is terminal and needs a human re-approval"),
            value=lambda: metrics.executions_total().value(outcome="failed"),
        ),
        AlertRule(
            name="StaleExecutionRecovered",
            severity="page",
            message=(
                "a worker died holding an EXECUTING claim; the mutation outcome "
                "is unknown and a human must reconcile before re-approving"
            ),
            value=lambda: metrics.stale_recoveries_total().total(),
        ),
    ]


class AlertManager:
    """Fires each rule on the rising edge of its watched value.

    A rule whose value has increased since the last evaluation fires exactly
    once for that increase, so a stuck-high counter does not page forever, and
    every new failure pages again.
    """

    def __init__(self, rules: list[AlertRule] | None = None) -> None:
        self.rules = default_rules() if rules is None else rules
        self.history: list[Firing] = []
        self._last_seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def evaluate(self) -> list[Firing]:
        fired: list[Firing] = []
        with self._lock:
            for rule in self.rules:
                current = rule.value()
                previous = self._last_seen.get(rule.name, 0.0)
                self._last_seen[rule.name] = current
                if current > previous:
                    firing = Firing(rule.name, rule.severity, rule.message, current)
                    self.history.append(firing)
                    fired.append(firing)
        for firing in fired:
            logger.error(
                "alert.fired",
                rule=firing.rule,
                severity=firing.severity,
                value=firing.value,
                message=firing.message,
            )
        return fired

    def snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                {
                    "rule": firing.rule,
                    "severity": firing.severity,
                    "message": firing.message,
                    "value": firing.value,
                    "fired_at": firing.fired_at.isoformat(),
                }
                for firing in self.history
            ]
