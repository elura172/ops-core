from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from app.domain.orders import ALLOWED_TRANSITIONS, OrderStatus


class Resolution(StrEnum):
    """How an observed platform event relates to the status we already hold."""

    APPLY = "APPLY"
    UNCHANGED = "UNCHANGED"
    STALE = "STALE"
    CONFLICT = "CONFLICT"


class Outcome(StrEnum):
    """What the repository did with an event; mirrors the webhook response body."""

    APPLIED = "applied"
    UNCHANGED = "unchanged"
    DUPLICATE = "duplicate"
    STALE = "stale"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class EventResolution:
    resolution: Resolution
    skipped: tuple[OrderStatus, ...] = ()


@dataclass(frozen=True)
class ApplyOutcome:
    outcome: Outcome
    order_id: str
    status: OrderStatus | None = None
    skipped: tuple[OrderStatus, ...] = ()

    @property
    def applied(self) -> bool:
        return self.outcome is Outcome.APPLIED


def path_between(start: OrderStatus, target: OrderStatus) -> tuple[OrderStatus, ...] | None:
    """Shortest forward path through the lifecycle graph, or None if unreachable."""
    if start == target:
        return (start,)
    queue: deque[tuple[OrderStatus, ...]] = deque([(start,)])
    seen = {start}
    while queue:
        path = queue.popleft()
        for nxt in ALLOWED_TRANSITIONS[path[-1]]:
            if nxt in seen:
                continue
            extended = (*path, nxt)
            if nxt == target:
                return extended
            seen.add(nxt)
            queue.append(extended)
    return None


def classify(current: OrderStatus, incoming: OrderStatus) -> EventResolution:
    """Decide how to fold an out-of-order platform event into local state.

    Webhook delivery is not ordered, so an event that is not an adjacent transition
    is normal traffic rather than an error. Forward jumps fast-forward (recording the
    states we never observed), events we are already past are stale, and events that
    are unreachable in either direction are genuine conflicts for a human to inspect.
    """
    if current == incoming:
        return EventResolution(Resolution.UNCHANGED)
    forward = path_between(current, incoming)
    if forward is not None:
        return EventResolution(Resolution.APPLY, forward[1:-1])
    if path_between(incoming, current) is not None:
        return EventResolution(Resolution.STALE)
    return EventResolution(Resolution.CONFLICT)
