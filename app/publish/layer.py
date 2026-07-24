from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PublishRequest:
    publish_id: str
    resource_type: str
    resource_id: str
    payload: dict


@dataclass(frozen=True)
class PublishResult:
    publish_id: str
    external_id: str | None
    dry_run: bool


class Publisher(Protocol):
    def publish(self, request: PublishRequest) -> PublishResult: ...


class DryRunPublisher:
    allowed_types = frozenset({"product_draft", "inventory", "price", "media_draft"})

    def publish(self, request: PublishRequest) -> PublishResult:
        if request.resource_type not in self.allowed_types:
            raise ValueError("resource type is not allowlisted")
        if not request.publish_id or not request.resource_id:
            raise ValueError("publish and resource IDs are required")
        return PublishResult(request.publish_id, f"dry-run://{request.resource_type}/{request.resource_id}", True)


class PublishGate:
    def __init__(self, publisher: Publisher, *, enabled: bool = False):
        self.publisher = publisher
        self.enabled = enabled

    def execute(self, request: PublishRequest, *, approved: bool) -> PublishResult:
        if not approved:
            raise PermissionError("publish request requires explicit approval")
        if not self.enabled:
            raise PermissionError("publishing is disabled")
        return self.publisher.publish(request)
