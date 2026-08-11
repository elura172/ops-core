"""In-process metrics with a Prometheus text exposition, no client library.

The registry is deliberately tiny: counters and one histogram shape, rendered
in the exposition format any Prometheus-compatible scraper accepts. Metrics are
process-local; when several API replicas run, each exposes its own ``/metrics``
and the scraper aggregates, which is exactly how Prometheus expects it.
"""

from __future__ import annotations

import threading
from bisect import bisect_left

_DEFAULT_BUCKETS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def _labels_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(labels.items()))


def _render_labels(key: tuple[tuple[str, str], ...], extra: str = "") -> str:
    parts = [f'{name}="{value}"' for name, value in key]
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


class Counter:
    def __init__(self, name: str, help_text: str) -> None:
        self.name = name
        self.help_text = help_text
        self._values: dict[tuple[tuple[str, str], ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError("counters only go up")
        key = _labels_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        with self._lock:
            return self._values.get(_labels_key(labels), 0.0)

    def total(self) -> float:
        with self._lock:
            return sum(self._values.values())

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} counter"]
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0")
            for key in sorted(self._values):
                lines.append(f"{self.name}{_render_labels(key)} {self._values[key]:g}")
        return lines


class Histogram:
    def __init__(
        self, name: str, help_text: str, buckets: tuple[float, ...] = _DEFAULT_BUCKETS
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.buckets = buckets
        self._counts = [0] * (len(buckets) + 1)
        self._sum = 0.0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        index = bisect_left(self.buckets, value)
        with self._lock:
            self._counts[index] += 1
            self._sum += value

    def count(self) -> int:
        with self._lock:
            return sum(self._counts)

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} histogram"]
        with self._lock:
            cumulative = 0
            for bound, bucket_count in zip(self.buckets, self._counts, strict=False):
                cumulative += bucket_count
                lines.append(f'{self.name}_bucket{{le="{bound:g}"}} {cumulative}')
            cumulative += self._counts[-1]
            lines.append(f'{self.name}_bucket{{le="+Inf"}} {cumulative}')
            lines.append(f"{self.name}_sum {self._sum:g}")
            lines.append(f"{self.name}_count {cumulative}")
        return lines


class Registry:
    def __init__(self) -> None:
        self._metrics: dict[str, Counter | Histogram] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, help_text: str) -> Counter:
        with self._lock:
            metric = self._metrics.get(name)
            if metric is None:
                metric = Counter(name, help_text)
                self._metrics[name] = metric
            if not isinstance(metric, Counter):
                raise TypeError(f"{name} is already registered as a histogram")
            return metric

    def histogram(self, name: str, help_text: str) -> Histogram:
        with self._lock:
            metric = self._metrics.get(name)
            if metric is None:
                metric = Histogram(name, help_text)
                self._metrics[name] = metric
            if not isinstance(metric, Histogram):
                raise TypeError(f"{name} is already registered as a counter")
            return metric

    def render(self) -> str:
        with self._lock:
            metrics = [self._metrics[name] for name in sorted(self._metrics)]
        lines: list[str] = []
        for metric in metrics:
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        """Testing hook; production code never resets counters."""
        with self._lock:
            self._metrics.clear()


#: The process-wide registry every component instruments against.
REGISTRY = Registry()


def executions_total() -> Counter:
    return REGISTRY.counter(
        "ops_review_executions_total",
        "Approved-action execution attempts by outcome "
        "(completed, failed, replayed, claim_rejected).",
    )


def execution_seconds() -> Histogram:
    return REGISTRY.histogram(
        "ops_review_execution_seconds",
        "Latency of the executed mutation, claim to completion.",
    )


def stale_recoveries_total() -> Counter:
    return REGISTRY.counter(
        "ops_review_stale_recoveries_total",
        "EXECUTING items moved to FAILED because their worker never finished; "
        "each one is an interrupted mutation with an unknown outcome.",
    )
