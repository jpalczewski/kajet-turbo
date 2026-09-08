"""Owner-facing management of a note's public share links -- thin glue over
NoteShareLinkRepository, mirroring SshKeyService's shape (repo call in, plain dict out).
The public read path (api/public_notes.py) calls the repository directly instead of this
service; that route has no owner/note target to authorize against, so there is nothing of
this service's shape for it to reuse."""

from kajet_turbo.models import NoteShareLink
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
from kajet_turbo.services.targets import NoteTarget


class NoteShareLinkService:
    def __init__(self, repo: NoteShareLinkRepository) -> None:
        self._repo = repo

    @staticmethod
    def _view(
        link: NoteShareLink,
        *,
        visit_count: int = 0,
        last_visited_at: str | None = None,
    ) -> dict:
        return {
            "token": link.token,
            "created_at": link.created_at,
            "visit_count": visit_count,
            "last_visited_at": last_visited_at,
        }

    def create(self, target: NoteTarget) -> dict:
        link = self._repo.create(target.note_id, target.workspace.name, target.workspace.owner_id)
        return self._view(link)

    def list_active(self, target: NoteTarget) -> list[dict]:
        return [
            self._view(
                summary.link,
                visit_count=summary.visit_count,
                last_visited_at=summary.last_visited_at,
            )
            for summary in self._repo.list_active_with_visit_summary(target.note_id)
        ]

    def revoke(self, target: NoteTarget, token: str) -> bool:
        # One round-trip: the repository guard enforces owner scope, note scope
        # (a token belonging to a different note the same owner controls must
        # not revoke), and the not-already-revoked check in a single fetch.
        return self._repo.revoke(target.workspace.owner_id, target.note_id, token)
