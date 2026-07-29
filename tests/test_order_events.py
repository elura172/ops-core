import pytest

from app.domain.order_events import Resolution, classify, path_between
from app.domain.orders import ALLOWED_TRANSITIONS, OrderStatus

S = OrderStatus


@pytest.mark.parametrize(
    ("current", "incoming", "expected"),
    [
        (S.RECEIVED, S.PAID, Resolution.APPLY),
        (S.RECEIVED, S.SHIPPED, Resolution.APPLY),
        (S.PAID, S.PAID, Resolution.UNCHANGED),
        (S.DELIVERED, S.PAID, Resolution.STALE),
        (S.SHIPPED, S.LABEL_CREATED, Resolution.STALE),
        (S.DELIVERED, S.CANCELLED, Resolution.CONFLICT),
        (S.CANCELLED, S.SHIPPED, Resolution.CONFLICT),
    ],
)
def test_classification(current, incoming, expected):
    assert classify(current, incoming).resolution is expected


def test_fast_forward_reports_only_the_states_never_observed():
    decision = classify(S.RECEIVED, S.SHIPPED)
    assert decision.skipped == (S.PAID, S.FULFILLMENT_PENDING, S.LABEL_CREATED)
    assert S.RECEIVED not in decision.skipped
    assert S.SHIPPED not in decision.skipped


def test_every_status_pair_classifies_without_raising():
    for current in OrderStatus:
        for incoming in OrderStatus:
            assert classify(current, incoming).resolution in set(Resolution)


def test_adjacent_transitions_are_always_applicable():
    for current, targets in ALLOWED_TRANSITIONS.items():
        for target in targets:
            decision = classify(current, target)
            assert decision.resolution is Resolution.APPLY
            assert decision.skipped == ()


def test_terminal_states_are_unreachable_from_each_other():
    assert path_between(S.DELIVERED, S.CANCELLED) is None
    assert path_between(S.CANCELLED, S.DELIVERED) is None
