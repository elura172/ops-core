from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded, jittered backoff shared by every outbound client."""

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.base_delay <= 0 or self.max_delay < self.base_delay:
            raise ValueError("retry policy bounds are invalid")

    def backoff(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay)
        # Full jitter: identical failures across workers must not resynchronise.
        return random.uniform(0, min(self.base_delay * (2 ** (attempt - 1)), self.max_delay))
