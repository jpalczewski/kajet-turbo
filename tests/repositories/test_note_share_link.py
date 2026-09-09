from sqlmodel import Session

from kajet_turbo.db import Database
from kajet_turbo.models import Note
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
from tests.conftest import seed_user


def _note(engine, note_id: str, workspace: str, owner_id: str) -> None:
    with Session(engine) as session:
        session.add(
            Note(
                id=note_id,
                workspace=workspace,
                owner_id=owner_id,
                title=note_id,
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.commit()


def test_create_and_resolve(database: Database):
    seed_user(database, "u1")
    _note(database.engine, "n1", "ws1", "u1")
    repo = NoteShareLinkRepository(database.engine)
    link = repo.create("n1", "ws1", "u1")
    assert link.token
    assert link.revoked_at is None

    resolved = repo.resolve(link.token)
    assert resolved is not None
    assert resolved.note_id == "n1"
    assert resolved.workspace == "ws1"
    assert resolved.owner_id == "u1"


def test_create_defaults_preview_description_to_false(database: Database):
    seed_user(database, "u1")
    _note(database.engine, "n1", "ws1", "u1")
    repo = NoteShareLinkRepository(database.engine)
    link = repo.create("n1", "ws1", "u1")
    assert link.preview_description is False


def test_create_accepts_preview_description(database: Database):
    seed_user(database, "u1")
    _note(database.engine, "n1", "ws1", "u1")
    repo = NoteShareLinkRepository(database.engine)
    link = repo.create("n1", "ws1", "u1", preview_description=True)
    assert link.preview_description is True


def test_resolve_unknown_token_returns_none(database: Database):
    repo = NoteShareLinkRepository(database.engine)
    assert repo.resolve("does-not-exist") is None


def test_list_for_note_and_user(database: Database):
    seed_user(database, "u1")
    _note(database.engine, "n1", "ws1", "u1")
    _note(database.engine, "n2", "ws1", "u1")
    repo = NoteShareLinkRepository(database.engine)
    link1 = repo.create("n1", "ws1", "u1")
    link2 = repo.create("n1", "ws1", "u1")
    link3 = repo.create("n2", "ws1", "u1")

    for_note = repo.list_for_note("n1")
    assert {link.token for link in for_note} == {link1.token, link2.token}

    for_user = repo.list_for_user("u1")
    assert {link.token for link in for_user} == {link1.token, link2.token, link3.token}


def test_revoke_is_owner_scoped(database: Database):
    seed_user(database, "u1")
    seed_user(database, "u2")
    _note(database.engine, "n1", "ws1", "u1")
    repo = NoteShareLinkRepository(database.engine)
    link = repo.create("n1", "ws1", "u1")

    assert repo.revoke("u2", "n1", link.token) is False  # not owner
    assert repo.resolve(link.token) is not None  # untouched

    assert repo.revoke("u1", "n1", link.token) is True
    assert repo.resolve(link.token) is None  # revoked token is not live

    assert repo.revoke("u1", "n1", link.token) is False  # already revoked, second call is a no-op


def test_revoke_is_note_scoped(database: Database):
    seed_user(database, "u1")
    _note(database.engine, "n1", "ws1", "u1")
    _note(database.engine, "n2", "ws1", "u1")
    repo = NoteShareLinkRepository(database.engine)
    link = repo.create("n1", "ws1", "u1")

    assert repo.revoke("u1", "n2", link.token) is False  # another note of the same owner
    assert repo.resolve(link.token) is not None  # untouched

    assert repo.revoke("u1", "n1", link.token) is True


def test_revoke_unknown_token_returns_false(database: Database):
    repo = NoteShareLinkRepository(database.engine)
    assert repo.revoke("u1", "n1", "does-not-exist") is False


def test_set_preview_description_is_owner_and_note_scoped(database: Database):
    seed_user(database, "u1")
    seed_user(database, "u2")
    _note(database.engine, "n1", "ws1", "u1")
    _note(database.engine, "n2", "ws1", "u1")
    repo = NoteShareLinkRepository(database.engine)
    link = repo.create("n1", "ws1", "u1")

    assert repo.set_preview_description("u2", "n1", link.token, True) is False  # not owner
    assert repo.set_preview_description("u1", "n2", link.token, True) is False  # wrong note
    resolved = repo.resolve(link.token)
    assert resolved is not None and resolved.preview_description is False  # untouched

    assert repo.set_preview_description("u1", "n1", link.token, True) is True
    resolved = repo.resolve(link.token)
    assert resolved is not None and resolved.preview_description is True

    assert repo.set_preview_description("u1", "n1", link.token, False) is True
    resolved = repo.resolve(link.token)
    assert resolved is not None and resolved.preview_description is False


def test_set_preview_description_rejects_revoked_token(database: Database):
    seed_user(database, "u1")
    _note(database.engine, "n1", "ws1", "u1")
    repo = NoteShareLinkRepository(database.engine)
    link = repo.create("n1", "ws1", "u1")
    repo.revoke("u1", "n1", link.token)

    assert repo.set_preview_description("u1", "n1", link.token, True) is False


def test_delete_for_note_in_session_removes_only_that_notes_links(database: Database):
    seed_user(database, "u1")
    _note(database.engine, "n1", "ws1", "u1")
    _note(database.engine, "n2", "ws1", "u1")
    repo = NoteShareLinkRepository(database.engine)
    link1 = repo.create("n1", "ws1", "u1")
    link2 = repo.create("n2", "ws1", "u1")

    with Session(database.engine) as session:
        repo.delete_for_note_in_session(session, "n1")
        session.commit()

    assert repo.resolve(link1.token) is None
    assert repo.list_for_note("n1") == []
    assert repo.resolve(link2.token) is not None


def test_delete_for_workspace_in_session_is_owner_and_workspace_scoped(database: Database):
    seed_user(database, "u1")
    seed_user(database, "u2")
    _note(database.engine, "n1", "ws1", "u1")
    _note(database.engine, "n2", "ws2", "u1")
    _note(database.engine, "n3", "ws1", "u2")
    repo = NoteShareLinkRepository(database.engine)
    same_ws_same_owner = repo.create("n1", "ws1", "u1")
    other_ws_same_owner = repo.create("n2", "ws2", "u1")
    same_ws_other_owner = repo.create("n3", "ws1", "u2")

    with Session(database.engine) as session:
        repo.delete_for_workspace_in_session(session, "ws1", "u1")
        session.commit()

    assert repo.resolve(same_ws_same_owner.token) is None
    assert repo.resolve(other_ws_same_owner.token) is not None
    assert repo.resolve(same_ws_other_owner.token) is not None
