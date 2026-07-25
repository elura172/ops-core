from __future__ import annotations


class CopyProviderError(RuntimeError):
    """Base for every failure a copy provider can raise."""


class TransientProviderError(CopyProviderError):
    """Retryable: a timeout, a network error, or a 5xx."""


class RateLimited(TransientProviderError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TerminalProviderError(CopyProviderError):
    """Not retryable: bad request, bad credentials, unusable response."""


class UsageLimitExceeded(CopyProviderError):
    """The configured call or token budget is spent; no request was made."""


class PolicyViolation(CopyProviderError):
    """Generated copy failed the product policy gate and must not be used."""

    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("generated copy failed the policy gate: " + "; ".join(reasons))
        self.reasons = reasons
