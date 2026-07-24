import pytest

from app.agents.models import AgentRole, TaskStatus
from app.agents.orchestrator import AgentOrchestrator
from app.domain.audit import AuditLog


def test_orchestrator_runs_bounded_role_task_and_audits():
    audit = AuditLog()
    orchestrator = AgentOrchestrator(audit)
    orchestrator.register(
        AgentRole.DISCOVERY, "rank_trends", lambda data: {"count": len(data["items"])}
    )
    task = orchestrator.submit(AgentRole.DISCOVERY, "rank_trends", {"items": [1, 2]})
    result = orchestrator.run(task.task_id)
    assert result.status == TaskStatus.COMPLETED
    assert result.output == {"count": 2}
    assert len(audit.events) == 2


def test_unallowlisted_operation_cannot_register():
    with pytest.raises(ValueError):
        AgentOrchestrator(AuditLog()).register(AgentRole.MEDIA, "publish_ad", lambda _: {})


def test_failed_task_cannot_handoff():
    orchestrator = AgentOrchestrator(AuditLog())
    orchestrator.register(
        AgentRole.ANALYTICS, "report_margin", lambda _: (_ for _ in ()).throw(RuntimeError("bad"))
    )
    task = orchestrator.submit(AgentRole.ANALYTICS, "report_margin", {})
    orchestrator.run(task.task_id)
    assert task.status == TaskStatus.FAILED
    with pytest.raises(ValueError):
        orchestrator.handoff(task.task_id, AgentRole.REVIEW, "review", {})
