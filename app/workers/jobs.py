from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable


@dataclass
class Job:
    job_id: str
    operation: str
    run: Callable[[], None]
    max_attempts: int = 3
    attempts: int = 0
    next_attempt_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_error: str | None = None


class JobRunner:
    """Deterministic retry runner; replace the queue backend without changing job contracts."""

    def __init__(self) -> None:
        self.pending: dict[str, Job] = {}
        self.dead_letter: list[Job] = []

    def enqueue(self, job: Job) -> None:
        if job.job_id in self.pending:
            return
        if job.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.pending[job.job_id] = job

    def run_due(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(timezone.utc)
        completed: list[str] = []
        for job_id, job in list(self.pending.items()):
            if job.next_attempt_at > now:
                continue
            job.attempts += 1
            try:
                job.run()
            except Exception as exc:  # noqa: BLE001 - queue must capture task failures
                job.last_error = str(exc)
                if job.attempts >= job.max_attempts:
                    self.dead_letter.append(job)
                    del self.pending[job_id]
                else:
                    job.next_attempt_at = now + timedelta(seconds=2 ** (job.attempts - 1))
            else:
                completed.append(job_id)
                del self.pending[job_id]
        return completed
