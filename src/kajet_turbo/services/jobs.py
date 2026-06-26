"""Read/act on the user's background jobs for the dashboard. Projects rows into a
curated view — the raw payload (which holds the filesystem ws_path) is never
exposed; only the workspace name is surfaced, parsed from the payload."""

import json

from kajet_turbo.models import Job
from kajet_turbo.repositories.jobs import JobRepository


class JobService:
    def __init__(self, job_repo: JobRepository):
        self._jobs = job_repo

    @staticmethod
    def _view(job: Job) -> dict:
        workspace = None
        if job.payload:
            try:
                workspace = json.loads(job.payload).get("workspace")
            except ValueError, TypeError:
                workspace = None
        return {
            "id": job.id,
            "kind": job.kind,
            "workspace": workspace,
            "status": job.status,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "last_error": job.last_error,
            "next_run_at": job.next_run_at,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    def list(self, user_id: str, *, status: str | None = None, limit: int = 50) -> list[dict]:
        return [self._view(j) for j in self._jobs.list_jobs(user_id, status=status, limit=limit)]

    def retry(self, user_id: str, job_id: str) -> bool:
        return self._jobs.retry(job_id, user_id)

    def dismiss(self, user_id: str, job_id: str) -> bool:
        return self._jobs.dismiss(job_id, user_id)
