from sqlmodel import Session

from kajet_turbo.db import Database
from kajet_turbo.models import Note, User
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository


def _user(engine, user_id: str) -> None:
    with Session(engine) as session:
        session.add(User(id=user_id, email=f"{user_id}@e.com", created_at="2026-01-01"))
        session.commit()


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
    _user(database.engine, "u1")
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


def test_resolve_unknown_token_returns_none(database: Database):
    repo = NoteShareLinkRepository(database.engine)
    assert repo.resolve("does-not-exist") is None


def test_list_for_note_and_user(database: Database):
    _user(database.engine, "u1")
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
    _user(database.engine, "u1")
    _user(database.engine, "u2")
    _note(database.engine, "n1", "ws1", "u1")
    repo = NoteShareLinkRepository(database.engine)
    link = repo.create("n1", "ws1", "u1")

    assert repo.revoke("u2", link.token) is False  # not owner
    assert repo.resolve(link.token) is not None  # untouched

    assert repo.revoke("u1", link.token) is True
    assert repo.resolve(link.token) is None  # revoked token is not live

    assert repo.revoke("u1", link.token) is False  # already revoked, second call is a no-op


def test_revoke_unknown_token_returns_false(database: Database):
    repo = NoteShareLinkRepository(database.engine)
    assert repo.revoke("u1", "does-not-exist") is False
