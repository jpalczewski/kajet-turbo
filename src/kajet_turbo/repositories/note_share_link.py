"""Repository for opaque, revocable note share tokens. ``resolve`` is the read path a
public share-link endpoint would call; it treats a revoked token as not found."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, func
from sqlmodel import Session, col, select

from kajet_turbo.models import NoteShareLink, NoteShareLinkVisit
from kajet_turbo.repositories import DbRepository


@dataclass(frozen=True, slots=True)
class ShareLinkVisitSummary:
    link: NoteShareLink
    visit_count: int
    last_visited_at: str | None
    page_view_count: int
    last_page_viewed_at: str | None


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

    def list_active_with_visit_summary(self, note_id: str) -> list[ShareLinkVisitSummary]:
        """Return active links and their visit aggregates in one query."""
        with self.timed_session() as session:
            # add_columns returns a SQLAlchemy Select outside SQLModel.exec's overloads.
            rows = session.execute(  # ty: ignore[deprecated] - SQLAlchemy Select
                select(
                    NoteShareLink,
                    func.count(col(NoteShareLinkVisit.id)).filter(
                        col(NoteShareLinkVisit.kind) == "content"
                    ),
                    func.max(col(NoteShareLinkVisit.created_at)).filter(
                        col(NoteShareLinkVisit.kind) == "content"
                    ),
                )
                .add_columns(
                    func.count(col(NoteShareLinkVisit.id)).filter(
                        col(NoteShareLinkVisit.kind) == "page"
                    ),
                    func.max(col(NoteShareLinkVisit.created_at)).filter(
                        col(NoteShareLinkVisit.kind) == "page"
                    ),
                )
                .outerjoin(
                    NoteShareLinkVisit,
                    col(NoteShareLinkVisit.token) == col(NoteShareLink.token),
                )
                .where(col(NoteShareLink.note_id) == note_id)
                .where(col(NoteShareLink.revoked_at).is_(None))
                .group_by(
                    col(NoteShareLink.token),
                    col(NoteShareLink.note_id),
                    col(NoteShareLink.workspace),
                    col(NoteShareLink.owner_id),
                    col(NoteShareLink.created_at),
                    col(NoteShareLink.revoked_at),
                    col(NoteShareLink.preview_description),
                )
                .order_by(col(NoteShareLink.created_at))
            ).all()
        return [
            ShareLinkVisitSummary(
                link=link,
                visit_count=int(visit_count),
                last_visited_at=last_visited_at,
                page_view_count=int(page_view_count),
                last_page_viewed_at=last_page_viewed_at,
            )
            for link, visit_count, last_visited_at, page_view_count, last_page_viewed_at in rows
        ]

    def record_visit(
        self,
        token: str,
        ip: str | None,
        user_agent: str | None,
        *,
        kind: Literal["content", "page"] = "content",
    ) -> None:
        """Persist one served read without putting capability or PII values in logs."""
        with self.operation("record_visit") as operation:
            session = operation.session
            session.add(
                NoteShareLinkVisit(
                    token=token,
                    kind=kind,
                    ip=ip,
                    user_agent=user_agent,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )
            session.commit()

    def sweep_visits(self, older_than_s: float) -> int:
        cutoff = (datetime.now(UTC) - timedelta(seconds=older_than_s)).isoformat()
        with self.operation("sweep_visits") as operation:
            session = operation.session
            result = session.execute(  # ty: ignore[deprecated] - DELETE statement
                delete(NoteShareLinkVisit).where(col(NoteShareLinkVisit.created_at) < cutoff)
            )
            session.commit()
            count = result.rowcount  # ty: ignore[unresolved-attribute] - CursorResult has rowcount
            operation.suppress_log()
            return count

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
    def delete_visits_for_note_in_session(session: Session, note_id: str) -> None:
        tokens = select(NoteShareLink.token).where(col(NoteShareLink.note_id) == note_id)
        session.exec(delete(NoteShareLinkVisit).where(col(NoteShareLinkVisit.token).in_(tokens)))

    @staticmethod
    def delete_for_workspace_in_session(session: Session, workspace: str, owner_id: str) -> None:
        session.exec(
            delete(NoteShareLink).where(
                col(NoteShareLink.workspace) == workspace,
                col(NoteShareLink.owner_id) == owner_id,
            )
        )

    @staticmethod
    def delete_visits_for_workspace_in_session(
        session: Session, workspace: str, owner_id: str
    ) -> None:
        tokens = select(NoteShareLink.token).where(
            col(NoteShareLink.workspace) == workspace,
            col(NoteShareLink.owner_id) == owner_id,
        )
        session.exec(delete(NoteShareLinkVisit).where(col(NoteShareLinkVisit.token).in_(tokens)))
