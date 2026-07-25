from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.agents.models import AGENT_SPECS, AgentRole, AgentTask, Handoff, TaskStatus
from app.domain.audit import AuditEvent, AuditLog

Handler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class AgentOrchestrator:
    audit: AuditLog

    def __post_init__(self) -> None:
        self.handlers: dict[tuple[AgentRole, str], Handler] = {}
        self.tasks: dict[str, AgentTask] = {}
        self.handoffs: list[Handoff] = []

    def register(self, role: AgentRole, operation: str, handler: Handler) -> None:
        spec = AGENT_SPECS[role]
        if operation not in spec.allowed_operations:
            raise ValueError(f"operation is not allowed for {role}: {operation}")
        self.handlers[(role, operation)] = handler

    def submit(self, role: AgentRole, operation: str, input: dict[str, Any]) -> AgentTask:
        if (role, operation) not in self.handlers:
            raise ValueError("no registered handler for agent operation")
        task = AgentTask(str(len(self.tasks) + 1), role, operation, input)
        self.tasks[task.task_id] = task
        return task

    def run(self, task_id: str) -> AgentTask:
        task = self.tasks[task_id]
        handler = self.handlers[(task.role, task.operation)]
        task.status = TaskStatus.RUNNING
        self.audit.record(
            AuditEvent(
                "agent.task.started",
                task.role.value,
                "started",
                task.task_id,
                {"correlation_id": task.correlation_id},
            )
        )
        try:
            output = handler(task.input)
            if not isinstance(output, dict):
                raise TypeError("agent handler must return a dictionary")
            if len(str(output)) > AGENT_SPECS[task.role].max_output_chars:
                raise ValueError("agent output exceeds configured limit")
            task.output = output
            task.status = TaskStatus.COMPLETED
            self.audit.record(
                AuditEvent(
                    "agent.task.completed",
                    task.role.value,
                    "completed",
                    task.task_id,
                    {"correlation_id": task.correlation_id},
                )
            )
        except Exception as exc:  # noqa: BLE001 - task boundary records failure
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            self.audit.record(
                AuditEvent(
                    "agent.task.failed",
                    task.role.value,
                    "failed",
                    task.task_id,
                    {"correlation_id": task.correlation_id},
                )
            )
        return task

    def handoff(
        self, task_id: str, to_role: AgentRole, purpose: str, payload: dict[str, Any]
    ) -> Handoff:
        task = self.tasks[task_id]
        if task.status != TaskStatus.COMPLETED:
            raise ValueError("only completed tasks may hand off")
        handoff = Handoff(task_id, to_role, purpose, payload)
        self.handoffs.append(handoff)
        self.audit.record(
            AuditEvent(
                "agent.handoff",
                task.role.value,
                "created",
                task_id,
                {"to_role": to_role.value, "correlation_id": task.correlation_id},
            )
        )
        return handoff
