"""Metrics exposition and rising-edge alert semantics."""

from __future__ import annotations

import pytest

from app.observability import metrics
from app.observability.alerts import AlertManager
from app.review.executor import ApprovedActionExecutor, ExecutionFailed
from app.review.queue import ReviewItem, ReviewQueue


@pytest.fixture(autouse=True)
def clean_registry():
    metrics.REGISTRY.reset()
    yield
    metrics.REGISTRY.reset()


class FlakyClient:
    dry_run = True

    def __init__(self, fail: bool = False):
        self.fail = fail

    def update_inventory(self, changes):
        if self.fail:
            raise RuntimeError("upstream down")
        return {"dry_run": True, "changes": changes}


def seeded(store, review_id="r-1"):
    store.submit(ReviewItem(review_id, "update_inventory", {"changes": {"sku-1": 3}}))
    store.decide(review_id, approve=True, reviewer="ada")
    return store


def test_counter_and_histogram_render_prometheus_text():
    metrics.executions_total().inc(outcome="completed")
    metrics.executions_total().inc(outcome="completed")
    metrics.execution_seconds().observe(0.004)
    rendered = metrics.REGISTRY.render()
    assert "# TYPE ops_review_executions_total counter" in rendered
    assert 'ops_review_executions_total{outcome="completed"} 2' in rendered
    assert 'ops_review_execution_seconds_bucket{le="0.005"} 1' in rendered
    assert "ops_review_execution_seconds_count 1" in rendered


def test_executor_counts_completed_replayed_and_failed():
    store = seeded(ReviewQueue())
    executor = ApprovedActionExecutor(FlakyClient())
    executor.execute(store, "r-1", actor="grace")
    executor.execute(store, "r-1", actor="grace")  # replays the stored result

    failing = seeded(ReviewQueue())
    with pytest.raises(ExecutionFailed):
        ApprovedActionExecutor(FlakyClient(fail=True)).execute(failing, "r-1", actor="grace")

    counter = metrics.executions_total()
    assert counter.value(outcome="completed") == 1
    assert counter.value(outcome="replayed") == 1
    assert counter.value(outcome="failed") == 1
    assert metrics.execution_seconds().count() == 1


def test_alerts_fire_on_the_rising_edge_only():
    manager = AlertManager()
    assert manager.evaluate() == []

    metrics.executions_total().inc(outcome="failed")
    fired = manager.evaluate()
    assert [firing.rule for firing in fired] == ["ReviewExecutionFailed"]
    # The counter is still high but has not risen; no re-page.
    assert manager.evaluate() == []

    metrics.executions_total().inc(outcome="failed")
    assert [firing.rule for firing in manager.evaluate()] == ["ReviewExecutionFailed"]


def test_metrics_and_alerts_endpoints_require_read_and_serve(settings):
    from fastapi.testclient import TestClient

    from app.api.http import create_app
    from app.api.operator import OperatorService
    from app.api.tiktok_client import TikTokClient
    from tests.conftest import REVIEWER_TOKEN, auth

    queue = seeded(ReviewQueue())
    operator = OperatorService(queue, ApprovedActionExecutor(TikTokClient("token", "shop", dry_run=True)))
    client = TestClient(create_app(operator=operator, settings=settings))

    assert client.get("/metrics").status_code == 401
    assert client.get("/alerts").status_code == 401

    body = client.get("/metrics", headers=auth(REVIEWER_TOKEN)).text
    assert "# TYPE ops_review_executions_total counter" in body
    assert client.get("/alerts", headers=auth(REVIEWER_TOKEN)).json() == []


def test_stale_recovery_alert_fires():
    manager = AlertManager()
    manager.evaluate()
    metrics.stale_recoveries_total().inc()
    fired = manager.evaluate()
    assert [firing.rule for firing in fired] == ["StaleExecutionRecovered"]
    snapshot = manager.snapshot()
    assert snapshot[-1]["rule"] == "StaleExecutionRecovered"
    assert snapshot[-1]["severity"] == "page"
