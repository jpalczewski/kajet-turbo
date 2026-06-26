from kajet_turbo.db import Database
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.users import UserRepository


def _user(database: Database) -> str:
    return UserRepository(database.engine).create("a@b.com", "hash")


def test_exists_false_when_empty(database: Database):
    repo = DanglingLinkRepository(database.engine)
    uid = _user(database)
    assert repo.exists(uid, "ws") is False


def test_replace_for_source_inserts_and_exists(database: Database):
    repo = DanglingLinkRepository(database.engine)
    uid = _user(database)
    repo.replace_for_source("src1", "ws", uid, [("", "Ghost"), ("Sub", "Other")])
    assert repo.exists(uid, "ws") is True
    rows = repo.list_for_workspace(uid, "ws")
    assert {(r["target_folder"], r["target_title"]) for r in rows} == {
        ("", "Ghost"),
        ("Sub", "Other"),
    }
    assert all(r["source_note_id"] == "src1" for r in rows)


def test_replace_for_source_replaces_prior_rows(database: Database):
    repo = DanglingLinkRepository(database.engine)
    uid = _user(database)
    repo.replace_for_source("src1", "ws", uid, [("", "Ghost")])
    repo.replace_for_source("src1", "ws", uid, [("", "Other")])  # replaces, not appends
    rows = repo.list_for_workspace(uid, "ws")
    assert {(r["target_folder"], r["target_title"]) for r in rows} == {("", "Other")}


def test_replace_for_source_empty_clears(database: Database):
    repo = DanglingLinkRepository(database.engine)
    uid = _user(database)
    repo.replace_for_source("src1", "ws", uid, [("", "Ghost")])
    repo.replace_for_source("src1", "ws", uid, [])  # clear
    assert repo.exists(uid, "ws") is False


def test_replace_for_source_isolates_other_sources(database: Database):
    repo = DanglingLinkRepository(database.engine)
    uid = _user(database)
    repo.replace_for_source("src1", "ws", uid, [("", "Ghost")])
    repo.replace_for_source("src2", "ws", uid, [("", "Phantom")])
    repo.replace_for_source("src1", "ws", uid, [])  # clearing src1 must not touch src2
    rows = repo.list_for_workspace(uid, "ws")
    assert {(r["source_note_id"], r["target_title"]) for r in rows} == {("src2", "Phantom")}


def test_delete_removes_one_row(database: Database):
    repo = DanglingLinkRepository(database.engine)
    uid = _user(database)
    repo.replace_for_source("src1", "ws", uid, [("", "Ghost"), ("", "Other")])
    rows = repo.list_for_workspace(uid, "ws")
    repo.delete(rows[0]["id"])
    remaining = repo.list_for_workspace(uid, "ws")
    assert len(remaining) == 1 and remaining[0]["id"] == rows[1]["id"]
