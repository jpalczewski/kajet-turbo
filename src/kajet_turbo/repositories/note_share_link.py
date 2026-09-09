"""Repository for opaque, revocable note share tokens. ``resolve`` is the read path a
public share-link endpoint would call; it treats a revoked token as not found."""

import secrets
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlmodel import Session, col, select

from kajet_turbo.models import NoteShareLink
from kajet_turbo.repositories import DbRepository


class NoteShareLinkRepository(DbRepository):
    repository_name = "note_share_links"

    def create(
        self,
        note_id: str,
        workspace: str,
        owner_id: str,
        preview_description: bool = False,
    ) -> NoteShareLink:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC).isoformat()
        with self.operation("create", note_id=note_id, owner_id=owner_id) as operation:
            session = operation.session
            link = NoteShareLink(
                token=token,
                note_id=note_id,
                workspace=workspace,
                owner_id=owner_id,
                created_at=now,
                preview_description=preview_description,
            )
            session.add(link)
            session.commit()
            session.refresh(link)
            return link

    def resolve(self, token: str) -> NoteShareLink | None:
        """Return the link if the token exists and is not revoked; ``None`` otherwise."""
        with self.timed_session() as session:
            link = session.get(NoteShareLink, token)
            return link if link is not None and link.revoked_at is None else None

    def list_for_note(self, note_id: str) -> list[NoteShareLink]:
        with self.timed_session() as session:
            return list(
                session.exec(
                    select(NoteShareLink)
                    .where(NoteShareLink.note_id == note_id)
                    .order_by(NoteShareLink.created_at)
                )
            )

    def list_for_user(self, owner_id: str) -> list[NoteShareLink]:
        with self.timed_session() as session:
            return list(
                session.exec(
                    select(NoteShareLink)
                    .where(NoteShareLink.owner_id == owner_id)
                    .order_by(NoteShareLink.created_at)
                )
            )

    def revoke(self, owner_id: str, note_id: str, token: str) -> bool:
        now = datetime.now(UTC).isoformat()

        def apply(session: Session, link: NoteShareLink) -> None:
            link.revoked_at = now
            session.add(link)

        return self._mutate_or_none(
            "revoke",
            NoteShareLink,
            token,
            apply,
            guard=lambda link: (
                link.owner_id == owner_id and link.note_id == note_id and link.revoked_at is None
            ),
            owner_id=owner_id,
        )

    def set_preview_description(self, owner_id: str, note_id: str, token: str, value: bool) -> bool:
        def apply(session: Session, link: NoteShareLink) -> None:
            link.preview_description = value
            session.add(link)

        return self._mutate_or_none(
            "set_preview_description",
            NoteShareLink,
            token,
            apply,
            guard=lambda link: (
                link.owner_id == owner_id and link.note_id == note_id and link.revoked_at is None
            ),
            owner_id=owner_id,
        )

    @staticmethod
    def delete_for_note_in_session(session: Session, note_id: str) -> None:
        session.exec(delete(NoteShareLink).where(col(NoteShareLink.note_id) == note_id))

    @staticmethod
    def delete_for_workspace_in_session(session: Session, workspace: str, owner_id: str) -> None:
        session.exec(
            delete(NoteShareLink).where(
                col(NoteShareLink.workspace) == workspace,
                col(NoteShareLink.owner_id) == owner_id,
            )
        )
