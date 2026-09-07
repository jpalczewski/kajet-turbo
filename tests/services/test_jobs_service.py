from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.services.jobs import JobService
from tests.conftest import seed_user


def _svc(database):
    seed_user(database, "u1")
    return JobService(JobRepository(database.engine))


def test_list_view_parses_workspace_from_payload(database):
    svc = _svc(database)
    JobRepository(database.engine).enqueue(
        "push_workspace",
        {"user_id": "u1", "workspace": "ws", "ws_path": "/secret/path"},
        dedup_key="u1:ws",
        user_id="u1",
        now=1000.0,
    )
    [view] = svc.list("u1")
    assert view["kind"] == "push_workspace"
    assert view["workspace"] == "ws"
    assert view["status"] == "pending"
    assert "ws_path" not in view and "payload" not in view  # raw path never exposed


def test_list_scoped_and_status_filter(database):
    svc = _svc(database)
    seed_user(database, "u2")
    repo = JobRepository(database.engine)
    repo.enqueue("k", {}, user_id="u1", now=1000.0)
    repo.enqueue("k", {}, user_id="u2", now=1000.0)
    assert len(svc.list("u1")) == 1  # only u1's jobs
    assert svc.list("u1", status="done") == []  # none done


def test_retry_and_dismiss_delegate(database):
    svc = _svc(database)
    repo = JobRepository(database.engine)
    job_id = repo.enqueue("k", {}, user_id="u1", max_attempts=1, now=1000.0)
    repo.claim("w", now=1000.0)
    repo.fail(job_id, "boom", now=1000.0)  # -> failed
    assert svc.retry("u1", job_id) is True
    assert svc.retry("u2", job_id) is False  # not owner
    # after retry it's pending -> dismiss rejects non-terminal
    assert svc.dismiss("u1", job_id) is False
