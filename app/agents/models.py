from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class AgentRole(StrEnum):
    DISCOVERY = "discovery"
    CATALOG = "catalog"
    MEDIA = "media"
    ANALYTICS = "analytics"
    OPERATIONS = "operations"
    REVIEW = "review"
    PUBLISH = "publish"
    SUPPORT = "support"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class AgentSpec:
    role: AgentRole
    allowed_operations: frozenset[str]
    max_steps: int = 10
    max_output_chars: int = 20_000


@dataclass
class AgentTask:
    task_id: str
    role: AgentRole
    operation: str
    input: dict[str, Any]
    correlation_id: str = field(default_factory=lambda: str(uuid4()))
    status: TaskStatus = TaskStatus.PENDING
    output: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class Handoff:
    from_task_id: str
    to_role: AgentRole
    purpose: str
    payload: dict[str, Any]


AGENT_SPECS = {
    AgentRole.DISCOVERY: AgentSpec(AgentRole.DISCOVERY, frozenset({"rank_trends", "summarize_signal"})),
    AgentRole.CATALOG: AgentSpec(AgentRole.CATALOG, frozenset({"build_product_draft", "check_margin"})),
    AgentRole.MEDIA: AgentSpec(AgentRole.MEDIA, frozenset({"build_ad_draft", "check_provenance"})),
    AgentRole.ANALYTICS: AgentSpec(AgentRole.ANALYTICS, frozenset({"report_funnel", "report_margin"})),
    AgentRole.OPERATIONS: AgentSpec(AgentRole.OPERATIONS, frozenset({"reconcile", "evaluate_sla"})),
    AgentRole.REVIEW: AgentSpec(AgentRole.REVIEW, frozenset({"submit_review", "summarize_review"})),
    AgentRole.PUBLISH: AgentSpec(AgentRole.PUBLISH, frozenset({"prepare_publish"})),
    AgentRole.SUPPORT: AgentSpec(AgentRole.SUPPORT, frozenset({"draft_customer_response"})),
}
