from datetime import UTC, datetime, timedelta

from app.domain.audit import AuditEvent, AuditLog
from app.workers.jobs import Job, JobRunner


def test_job_retries_then_succeeds():
    attempts = []
    runner = JobRunner()

    def task():
        attempts.append(1)
        if len(attempts) < 2:
            raise RuntimeError("temporary")

    runner.enqueue(Job("j-1", "sync", task))
    now = datetime.now(UTC)
    assert runner.run_due(now) == []
    assert runner.run_due(now + timedelta(seconds=2)) == ["j-1"]
    assert len(runner.dead_letter) == 0


def test_failed_job_moves_to_dead_letter():
    runner = JobRunner()
    runner.enqueue(Job("j-2", "sync", lambda: (_ for _ in ()).throw(RuntimeError("bad")), max_attempts=2))
    now = datetime.now(UTC)
    runner.run_due(now)
    runner.run_due(now + timedelta(seconds=2))
    assert not runner.pending
    assert runner.dead_letter[0].last_error == "bad"


def test_audit_is_structured_and_append_only():
    log = AuditLog()
    log.record(AuditEvent("test", "system", "ok", resource_id="r-1"))
    assert '"action": "test"' in log.events[0].to_json()
    assert len(log.events) == 1
