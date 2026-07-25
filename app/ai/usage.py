from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from app.ai.errors import UsageLimitExceeded
from app.observability.logging import get_logger

logger = get_logger("ai_usage")


@dataclass(frozen=True)
class UsageLimit:
    """A hard ceiling on model spend, enforced before each request.

    Token counts rather than currency: prices change independently of this code,
    and a wrong hardcoded price is worse than no price at all. Convert with your
    provider's current rate card when you set these.
    """

    max_calls: int = 100
    max_total_tokens: int = 250_000

    def __post_init__(self) -> None:
        if self.max_calls < 1 or self.max_total_tokens < 1:
            raise ValueError("usage limits must be positive")


@dataclass
class UsageLedger:
    """Process-local spend counter.

    Not shared between workers or restarts, so treat the effective ceiling as
    per-process. Back it with the review database if you need a global budget.
    """

    limit: UsageLimit = field(default_factory=UsageLimit)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def reserve(self) -> None:
        """Claim one call up front, so a crash mid-request still costs budget."""
        with self._lock:
            if self.calls >= self.limit.max_calls:
                raise UsageLimitExceeded(
                    f"call budget exhausted: {self.calls}/{self.limit.max_calls}"
                )
            if self.total_tokens >= self.limit.max_total_tokens:
                raise UsageLimitExceeded(
                    f"token budget exhausted: {self.total_tokens}/{self.limit.max_total_tokens}"
                )
            self.calls += 1

    def record(self, usage: dict[str, Any] | None) -> None:
        if not usage:
            return
        with self._lock:
            self.input_tokens += int(usage.get("input_tokens", 0) or 0)
            self.output_tokens += int(usage.get("output_tokens", 0) or 0)
            spent, budget = self.total_tokens, self.limit.max_total_tokens
        logger.info("ai.usage", calls=self.calls, tokens=spent, token_budget=budget)
        if spent >= budget:
            logger.warning("ai.usage.budget_exhausted", tokens=spent, token_budget=budget)
