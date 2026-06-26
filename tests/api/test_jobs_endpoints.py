from fastapi import FastAPI
from sqlmodel import Session
from starlette.testclient import TestClient

from kajet_turbo.api.jobs import router
from kajet_turbo.dependencies import get_job_service
from kajet_turbo.models import User
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.services.jobs import JobService


def _app(database, monkeypatch, *, user_id="u1"):
    if user_id:
        with Session(database.engine) as s:
            s.add(User(id=user_id, email="u@e.com", created_at="2026-01-01"))
            s.commit()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_job_service] = lambda: JobService(JobRepository(database.engine))
    monkeypatch.setattr(
        "kajet_turbo.api.jobs.get_session_user",
        lambda _r: {"id": user_id} if user_id else None,
    )
    return TestClient(app), JobRepository(database.engine)


def test_list_jobs(database, monkeypatch):
    client, repo = _app(database, monkeypatch)
    repo.enqueue("push_workspace", {"workspace": "ws"}, dedup_key="u1:ws", user_id="u1", now=1000.0)
    jobs = client.get("/api/me/jobs").json()["jobs"]
    assert [j["kind"] for j in jobs] == ["push_workspace"]
    assert jobs[0]["workspace"] == "ws"


def test_retry_failed_job(database, monkeypatch):
    client, repo = _app(database, monkeypatch)
    job_id = repo.enqueue("k", {}, user_id="u1", max_attempts=1, now=1000.0)
    repo.claim("w", now=1000.0)
    repo.fail(job_id, "boom", now=1000.0)  # failed
    assert client.post(f"/api/me/jobs/{job_id}/retry").status_code == 200
    # now pending -> retry again rejected
    assert client.post(f"/api/me/jobs/{job_id}/retry").status_code == 404


def test_dismiss_terminal_job(database, monkeypatch):
    client, repo = _app(database, monkeypatch)
    job_id = repo.enqueue("k", {}, user_id="u1", max_attempts=1, now=1000.0)
    repo.claim("w", now=1000.0)
    repo.fail(job_id, "boom", now=1000.0)  # failed (terminal)
    assert client.delete(f"/api/me/jobs/{job_id}").status_code == 200
    assert client.delete(f"/api/me/jobs/{job_id}").status_code == 404  # gone


def test_requires_login(database, monkeypatch):
    client, _ = _app(database, monkeypatch, user_id=None)
    assert client.get("/api/me/jobs").status_code == 401
