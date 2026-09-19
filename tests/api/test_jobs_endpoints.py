from starlette.testclient import TestClient

from kajet_turbo.api.jobs import router
from kajet_turbo.dependencies import get_job_service
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.services.jobs import JobService
from tests.api.conftest import build_test_app
from tests.conftest import seed_user


def _app(database, monkeypatch, *, user_id="u1"):
    seed_user(database, user_id)
    app = build_test_app(routers=(router,), user_id=user_id)
    app.dependency_overrides[get_job_service] = lambda: JobService(JobRepository(database.engine))
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
    repo.fail(job_id, "w", "boom", now=1000.0)  # failed
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
    repo.fail(job_id, "w", "boom", now=1000.0)  # failed
    repo.enqueue("other", {}, user_id="u1", now=1000.0)  # pending

    jobs = client.get("/api/me/jobs", params={"status": ""}).json()["jobs"]
    assert {j["kind"] for j in jobs} == {"k", "other"}


def test_retry_failed_job(database, monkeypatch):
    client, repo = _app(database, monkeypatch)
    job_id = repo.enqueue("k", {}, user_id="u1", max_attempts=1, now=1000.0)
    repo.claim("w", now=1000.0)
    repo.fail(job_id, "w", "boom", now=1000.0)  # failed
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
    repo.fail(job_id, "w", "boom", now=1000.0)  # failed (terminal)
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


def test_requires_login(anon_client):
    assert anon_client.get("/api/me/jobs").status_code == 401
    assert anon_client.post("/api/me/jobs/x/retry").status_code == 401
    assert anon_client.delete("/api/me/jobs/x").status_code == 401
