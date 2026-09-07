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
    def _view(link: NoteShareLink) -> dict:
        return {"token": link.token, "created_at": link.created_at}

    def create(self, target: NoteTarget) -> dict:
        link = self._repo.create(target.note_id, target.workspace.name, target.workspace.owner_id)
        return self._view(link)

    def list_active(self, target: NoteTarget) -> list[dict]:
        return [
            self._view(link)
            for link in self._repo.list_for_note(target.note_id)
            if link.revoked_at is None
        ]

    def revoke(self, target: NoteTarget, token: str) -> bool:
        # resolve() already treats a revoked/unknown token as not-found; the note_id
        # check on top of that is what stops this note-scoped route from revoking a
        # token that belongs to a different note the same owner controls.
        link = self._repo.resolve(token)
        if link is None or link.note_id != target.note_id:
            return False
        return self._repo.revoke(target.workspace.owner_id, token)
