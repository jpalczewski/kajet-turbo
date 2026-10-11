"""User notices: standing conditions of a user's setup, kept until fixed (epic #452).

A notice is keyed state, not an event. Each kind is a closed, typed payload of ids and
codes with a fixed message template — never free text, and never note titles or content
(#403): whatever lands here is later shown to a model in another conversation as if the
server said it. The key is derived from the payload, so a producer cannot invent one.

Every state change commits together with a ``notices_changed`` outbox event, published
only on a transition. Two hundred failed searches are one notice and one event.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.notices import NoticeRepository, NoticeRow

NOTICES_CHANGED = "notices_changed"

# An opaque identifier: what nanoid, uuid and slug ids look like, and nothing that reads
# as prose — no spaces, no punctuation beyond '-' and '_'.
type NoticeId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class NoticeKind(StrEnum):
    EMBEDDING_AUTH = "embedding_auth"


class NoticePayload(BaseModel):
    """Base for one kind's payload. Every field is part of the notice's identity, which
    is what keeps the key derivable; a kind that ever needs a non-identity field
    overrides ``key``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ClassVar[NoticeKind]
    template: ClassVar[str]

    def key(self) -> str:
        return ":".join([self.kind, *(str(v) for v in self.model_dump().values())])

    def message(self) -> str:
        return self.template.format(**self.model_dump())


class EmbeddingAuthNotice(NoticePayload):
    kind = NoticeKind.EMBEDDING_AUTH
    template = (
        "The embedding API rejected the API key of embedding profile {profile_id}. "
        "Semantic search runs keyword-only and new notes are not embedded until the "
        "key is updated."
    )

    profile_id: NoticeId


_PAYLOADS: dict[NoticeKind, type[NoticePayload]] = {cls.kind: cls for cls in (EmbeddingAuthNotice,)}


class NoticesChangedEvent(BaseModel):
    """Outbox event: a notice was raised, resolved or acknowledged. Deliberately carries
    no notice content — the client refetches the list. Lives here rather than in
    ``api/schemas/ws.py`` because the api package imports the service layer; the WS
    schema re-exports it once the web UI consumes it (N3)."""

    type: Literal["notices_changed"] = NOTICES_CHANGED
    owner_id: str


@dataclass(frozen=True, slots=True)
class ActiveNotice:
    id: str
    kind: NoticeKind
    payload: NoticePayload
    message: str
    count: int
    first_seen_at: float
    last_seen_at: float
    acknowledged_at: float | None


class NoticeService:
    def __init__(
        self,
        repo: NoticeRepository,
        events: EventRepository,
        clock: Callable[[], float] = time.time,
    ):
        self._repo = repo
        self._events = events
        self._clock = clock

    def raise_(self, user_id: str, payload: NoticePayload) -> None:
        payload = self._validated(payload)
        with self._repo.operation("raise", user_id=user_id, kind=payload.kind) as operation:
            session = operation.session
            raised = self._repo.raise_in_session(
                session,
                user_id,
                payload.kind,
                payload.key(),
                payload.model_dump_json(),
                self._clock(),
            )
            if raised.opened:
                self._publish_changed(session, user_id)
                operation.outcome = "opened"
                operation.add_fields(notice_id=raised.id)
            else:
                # The producer's own log line already records every occurrence; a second
                # one here per failed search would only double the volume.
                operation.outcome = "bumped"
                operation.suppress_log()
            session.commit()

    def resolve(self, user_id: str, payload: NoticePayload) -> None:
        payload = self._validated(payload)
        key = payload.key()
        if self._repo.find_active_id(user_id, key) is None:
            return
        with self._repo.operation("resolve", user_id=user_id, kind=payload.kind) as operation:
            session = operation.session
            # Re-checked under the write lock: a concurrent resolve may have won.
            if self._repo.resolve_in_session(session, user_id, key, self._clock()):
                self._publish_changed(session, user_id)
            else:
                operation.suppress_log()
            session.commit()

    def acknowledge(self, user_id: str, notice_id: str) -> None:
        """Raises ``NoticeNotFoundError`` (a ``ValueError``) when ``notice_id`` is not an
        active notice of this user."""
        with self._repo.operation("acknowledge", user_id=user_id, notice_id=notice_id) as operation:
            session = operation.session
            if self._repo.acknowledge_in_session(session, user_id, notice_id, self._clock()):
                self._publish_changed(session, user_id)
            else:
                operation.suppress_log()
            session.commit()

    def list_active(self, user_id: str) -> list[ActiveNotice]:
        return [n for row in self._repo.list_active(user_id) if (n := self._parse(row))]

    @staticmethod
    def _validated(payload: NoticePayload) -> NoticePayload:
        # Re-validated here, not trusted from the caller: model_construct() builds an
        # instance without running any validator, and this is the boundary that promises
        # no free text gets stored.
        return type(payload).model_validate(payload.model_dump())

    def _publish_changed(self, session: Session, user_id: str) -> None:
        event = NoticesChangedEvent(owner_id=user_id)
        self._events.publish_in_session(session, user_id, NOTICES_CHANGED, event.model_dump())

    @staticmethod
    def _parse(row: NoticeRow) -> ActiveNotice | None:
        try:
            kind = NoticeKind(row.kind)
            payload = _PAYLOADS[kind].model_validate(json.loads(row.payload))
        except ValueError as e:
            # A kind retired after its rows were written. Skip it rather than fail the
            # whole listing, which every transport sits on.
            logger.warning(
                "notice_unparseable", notice_id=row.id, kind=row.kind, error_type=type(e).__name__
            )
            return None
        return ActiveNotice(
            id=row.id,
            kind=kind,
            payload=payload,
            message=payload.message(),
            count=row.count,
            first_seen_at=row.first_seen_at,
            last_seen_at=row.last_seen_at,
            acknowledged_at=row.acknowledged_at,
        )


class EmbeddingKeyNotices:
    """The producer side of ``embedding_auth``, shared by search, the embed worker and
    profile management. Best-effort by contract: recording a notice must never fail the
    search or embed it rides on, so errors are logged and swallowed here, once."""

    def __init__(self, notices: NoticeService):
        self._notices = notices

    def rejected(self, user_id: str, profile_id: str | None) -> None:
        self._apply(self._notices.raise_, user_id, profile_id)

    def clear(self, user_id: str, profile_id: str | None) -> None:
        self._apply(self._notices.resolve, user_id, profile_id)

    @staticmethod
    def _apply(
        action: Callable[[str, NoticePayload], None], user_id: str, profile_id: str | None
    ) -> None:
        # No profile id: the config did not come from a stored profile, so there is
        # nothing the user could fix in one place and nothing to key a notice on.
        if profile_id is None:
            return
        try:
            action(user_id, EmbeddingAuthNotice(profile_id=profile_id))
        except Exception as e:
            logger.opt(exception=e).warning(
                "notice_update_failed", kind=NoticeKind.EMBEDDING_AUTH, user_id=user_id
            )
