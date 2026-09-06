from sqlmodel import Session
from starlette.testclient import TestClient

from kajet_turbo.api.jobs import router
from kajet_turbo.dependencies import CurrentUser, get_job_service, get_required_user
from kajet_turbo.models import User
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.services.jobs import JobService
from tests.api.conftest import build_test_app


def _app(database, monkeypatch, *, user_id="u1"):
    if user_id:
        with Session(database.engine) as s:
            s.add(User(id=user_id, email="u@e.com", created_at="2026-01-01"))
            s.commit()
    app = build_test_app(routers=(router,))
    app.dependency_overrides[get_job_service] = lambda: JobService(JobRepository(database.engine))
    if user_id:
        app.dependency_overrides[get_required_user] = lambda: CurrentUser(
            id=user_id, email="", timezone="", locale=""
        )
    return TestClient(app), JobRepository(database.engine)


def test_list_jobs(database, monkeypatch):
    client, repo = _app(database, monkeypatch)
    repo.enqueue("push_workspace", {"workspace": "ws"}, dedup_key="u1:ws", user_id="u1", now=1000.0)
    body = client.get("/api/me/jobs").json()
    assert set(body) == {"jobs"}
    jobs = body["jobs"]
    assert len(jobs) == 1
    assert set(jobs[0]) == {
        "id",
        "kind",
        "workspace",
        "status",
        "attempts",
        "max_attempts",
        "last_error",
        "next_run_at",
        "created_at",
        "updated_at",
    }
    assert jobs[0]["kind"] == "push_workspace"
    assert jobs[0]["workspace"] == "ws"
    assert jobs[0]["status"] == "pending"


def test_list_jobs_filters_by_status(database, monkeypatch):
    client, repo = _app(database, monkeypatch)
    job_id = repo.enqueue("k", {}, user_id="u1", max_attempts=1, now=1000.0)
    repo.claim("w", now=1000.0)
    repo.fail(job_id, "boom", now=1000.0)  # failed
    repo.enqueue("other", {}, user_id="u1", now=1000.0)  # pending

    failed = client.get("/api/me/jobs", params={"status": "failed"}).json()["jobs"]
    assert [j["kind"] for j in failed] == ["k"]

    pending = client.get("/api/me/jobs", params={"status": "pending"}).json()["jobs"]
    assert [j["kind"] for j in pending] == ["other"]


def test_list_jobs_empty_status_query_means_no_filter(database, monkeypatch):
    # A blank `?status=` (e.g. an HTML form's unset "All" option) must behave like the
    # status param being absent entirely, not like filtering for Job.status == "".
    client, repo = _app(database, monkeypatch)
    job_id = repo.enqueue("k", {}, user_id="u1", max_attempts=1, now=1000.0)
    repo.claim("w", now=1000.0)
    repo.fail(job_id, "boom", now=1000.0)  # failed
    repo.enqueue("other", {}, user_id="u1", now=1000.0)  # pending

    jobs = client.get("/api/me/jobs", params={"status": ""}).json()["jobs"]
    assert {j["kind"] for j in jobs} == {"k", "other"}


def test_retry_failed_job(database, monkeypatch):
    client, repo = _app(database, monkeypatch)
    job_id = repo.enqueue("k", {}, user_id="u1", max_attempts=1, now=1000.0)
    repo.claim("w", now=1000.0)
    repo.fail(job_id, "boom", now=1000.0)  # failed
    resp = client.post(f"/api/me/jobs/{job_id}/retry")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # now pending -> retry again rejected
    resp = client.post(f"/api/me/jobs/{job_id}/retry")
    assert resp.status_code == 404
    assert resp.json() == {"error": "JOB_NOT_FOUND"}


def test_retry_unknown_job_not_found(database, monkeypatch):
    client, _repo = _app(database, monkeypatch)
    resp = client.post("/api/me/jobs/does-not-exist/retry")
    assert resp.status_code == 404
    assert resp.json() == {"error": "JOB_NOT_FOUND"}


def test_dismiss_terminal_job(database, monkeypatch):
    client, repo = _app(database, monkeypatch)
    job_id = repo.enqueue("k", {}, user_id="u1", max_attempts=1, now=1000.0)
    repo.claim("w", now=1000.0)
    repo.fail(job_id, "boom", now=1000.0)  # failed (terminal)
    resp = client.delete(f"/api/me/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    resp = client.delete(f"/api/me/jobs/{job_id}")  # gone
    assert resp.status_code == 404
    assert resp.json() == {"error": "JOB_NOT_FOUND"}


def test_dismiss_unknown_job_not_found(database, monkeypatch):
    client, _repo = _app(database, monkeypatch)
    resp = client.delete("/api/me/jobs/does-not-exist")
    assert resp.status_code == 404
    assert resp.json() == {"error": "JOB_NOT_FOUND"}


def test_requires_login(database, monkeypatch):
    client, _ = _app(database, monkeypatch, user_id=None)
    assert client.get("/api/me/jobs").status_code == 401
    assert client.post("/api/me/jobs/x/retry").status_code == 401
    assert client.delete("/api/me/jobs/x").status_code == 401
