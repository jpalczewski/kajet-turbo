from pathlib import Path

from kajet_turbo.db import Database
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.services.heal_enqueue import make_enqueue_heal_on_commit


def test_enqueue_skips_when_no_dangling(database: Database, tmp_path: Path):
    jobs = JobRepository(database.engine)
    dangling = DanglingLinkRepository(database.engine)
    uid = UserRepository(database.engine).create("a@b.com", "h")
    base = tmp_path
    ws_path = str(base / uid / "ws")
    hook = make_enqueue_heal_on_commit(jobs, dangling, str(base))
    hook(ws_path)  # no dangling rows -> no job
    listed = jobs.list_jobs(uid)
    assert listed == [] or all(j.kind != "heal_dangling" for j in listed)


def test_enqueue_when_dangling_present(database: Database, tmp_path: Path):
    jobs = JobRepository(database.engine)
    dangling = DanglingLinkRepository(database.engine)
    uid = UserRepository(database.engine).create("a@b.com", "h")
    base = tmp_path
    ws_path = str(base / uid / "ws")
    dangling.replace_for_source("src", "ws", uid, [("", "Ghost")])
    hook = make_enqueue_heal_on_commit(jobs, dangling, str(base))
    hook(ws_path)
    kinds = [j.kind for j in jobs.list_jobs(uid)]
    assert "heal_dangling" in kinds


def test_enqueue_dedups_burst(database: Database, tmp_path: Path):
    jobs = JobRepository(database.engine)
    dangling = DanglingLinkRepository(database.engine)
    uid = UserRepository(database.engine).create("a@b.com", "h")
    base = tmp_path
    ws_path = str(base / uid / "ws")
    dangling.replace_for_source("src", "ws", uid, [("", "Ghost")])
    hook = make_enqueue_heal_on_commit(jobs, dangling, str(base))
    hook(ws_path)
    hook(ws_path)  # second commit coalesces into the one pending job
    heal = [j for j in jobs.list_jobs(uid) if j.kind == "heal_dangling"]
    assert len(heal) == 1
