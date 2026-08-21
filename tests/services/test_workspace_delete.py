from pathlib import Path

import pytest
from sqlmodel import Session, col, select

from kajet_turbo.markdown import Chunk
from kajet_turbo.models import (
    ActiveWorkspace,
    DanglingLink,
    FolderMeta,
    Note,
    NoteLink,
    NoteTag,
    Tag,
    WorkspaceAccess,
    WorkspaceMeta,
    WorkspaceRemote,
)
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteLinkRepository,
    NoteTagRepository,
)
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from tests.services.conftest import build_workspace_service, seed_user


def _seed_full_workspace(database, *, user_id: str, name: str) -> None:
    """Seeds one row in every workspace-scoped table, plus a WorkspaceRemote (which
    needs a real User + SshKey to satisfy FKs)."""
    seed_user(database, user_id)
    with Session(database.engine) as session:
        session.add(
            Note(
                id=f"{user_id}-n1",
                workspace=name,
                owner_id=user_id,
                title="T",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.commit()

    chunk_repo = NoteChunkRepository(database.engine)
    chunk_repo.replace_chunks(
        f"{user_id}-n1", name, user_id, "T", [Chunk(0, ["# T"], "body", 0, 4)], None, None
    )

    NoteTagRepository(database.engine).sync_note_tags(
        f"{user_id}-n1", name, user_id, [("proj/notes", "frontmatter")]
    )

    NoteLinkRepository(database.engine).replace_links(
        f"{user_id}-n1", name, user_id, {f"{user_id}-n2"}
    )

    DanglingLinkRepository(database.engine).replace_for_source(
        f"{user_id}-n1", name, user_id, [("", "Missing Note")]
    )

    FolderMetaRepository(database.engine).set(user_id, name, "proj", description="Project folder")

    ssh_repo = SshKeyRepository(database.engine)
    key = ssh_repo.create(user_id, "deploy", "ed25519", "ssh-ed25519 AAAA", b"secret", "fp")
    WorkspaceRemoteRepository(database.engine).upsert(
        user_id, name, origin_url="git@host:repo.git", ssh_key_id=key.id, enabled=True
    )

    active_repo = ActiveWorkspaceRepository(database.engine)
    active_repo.set(user_id, name)
    active_repo.set(user_id, name, scope="mcp-session-1")


def _counts(database, *, workspace: str, owner_id: str) -> dict[str, int]:
    with Session(database.engine) as session:

        def count(model, **filters) -> int:
            stmt = select(model)
            for key, value in filters.items():
                stmt = stmt.where(getattr(model, key) == value)
            return len(session.exec(stmt).all())

        return {
            "notes": count(Note, workspace=workspace, owner_id=owner_id),
            "note_tags": len(
                session.exec(
                    select(NoteTag)
                    .join(Tag, col(NoteTag.tag_id) == col(Tag.id))
                    .where(Tag.workspace == workspace, Tag.owner_id == owner_id)
                ).all()
            ),
            "tags": count(Tag, workspace=workspace, owner_id=owner_id),
            "note_links": count(NoteLink, workspace=workspace, owner_id=owner_id),
            "dangling_links": count(DanglingLink, workspace=workspace, owner_id=owner_id),
            "folder_meta": count(FolderMeta, workspace=workspace, owner_id=owner_id),
            "workspace_remote": count(WorkspaceRemote, workspace=workspace, user_id=owner_id),
            "active_workspace": count(ActiveWorkspace, workspace=workspace, user_id=owner_id),
            "workspace_access": count(WorkspaceAccess, workspace=workspace, user_id=owner_id),
            "workspace_meta": count(WorkspaceMeta, workspace=workspace, user_id=owner_id),
        }


def test_delete_wipes_every_table_and_the_directory(
    database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_workspace_factory
):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    ws_dir = git_workspace_factory("u1/ws")
    _seed_full_workspace(database, user_id="u1", name="ws")
    svc = build_workspace_service(database)
    svc._repo.grant_access("u1", "ws")
    svc._meta_repo.ensure("u1", "ws")

    before = _counts(database, workspace="ws", owner_id="u1")
    assert all(v > 0 for v in before.values()), before

    svc.delete("u1", "ws")

    after = _counts(database, workspace="ws", owner_id="u1")
    assert all(v == 0 for v in after.values()), after
    assert not ws_dir.exists()
    assert svc.has_access("u1", "ws") is False


def test_delete_is_owner_scoped(
    database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_workspace_factory
):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    u1_dir = git_workspace_factory("u1/ws")
    u2_dir = git_workspace_factory("u2/ws")
    _seed_full_workspace(database, user_id="u1", name="ws")
    _seed_full_workspace(database, user_id="u2", name="ws")
    svc = build_workspace_service(database)
    for uid in ("u1", "u2"):
        svc._repo.grant_access(uid, "ws")
        svc._meta_repo.ensure(uid, "ws")

    svc.delete("u1", "ws")

    assert not u1_dir.exists()
    assert u2_dir.exists()
    assert svc.has_access("u1", "ws") is False
    assert svc.has_access("u2", "ws") is True
    other = _counts(database, workspace="ws", owner_id="u2")
    assert all(v > 0 for v in other.values()), other


def test_delete_is_idempotent(
    database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_workspace_factory
):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    seed_user(database, "u1")
    git_workspace_factory("u1/ws")
    svc = build_workspace_service(database)
    svc._repo.grant_access("u1", "ws")
    svc._meta_repo.ensure("u1", "ws")

    svc.delete("u1", "ws")
    svc.delete("u1", "ws")  # must not raise on a second call


def test_delete_nonexistent_workspace_is_a_noop(
    database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("WORKSPACES_DIR", str(tmp_path))
    seed_user(database, "u1")
    svc = build_workspace_service(database)

    svc.delete("u1", "never-existed")  # must not raise
