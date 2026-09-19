"""REST target resolution must run under run_sync's DB-pool-sized limiter (#340).

The resolvers hit the DB (`has_access`, note lookup). As sync `def` dependencies they
ran on AnyIO's default 40-thread pool and bypassed the 10-slot limiter that is sized to
the SQLAlchemy pool. The stub resolvers below record the limiter's borrowed-token count
from inside the worker thread, so the test pins the behaviour, not the implementation.
"""

from pathlib import Path
from unittest.mock import MagicMock

import kajet_turbo.dependencies as rest_deps
from kajet_turbo import concurrency
from kajet_turbo.dependencies import CurrentUser
from kajet_turbo.services.targets import NoteTarget, TargetResolver, WorkspaceTarget

USER = CurrentUser(id="u-caller", email="u@example.com", timezone="UTC", locale="en")
WS = WorkspaceTarget(owner_id="u-caller", name="ws", path=Path("/tmp/ws"))
NOTE = NoteTarget(note_id="n1", workspace=WS)


def _resolver(borrowed: list[int]) -> MagicMock:
    limiter = concurrency._db_limiter()

    def workspace(*_args) -> WorkspaceTarget:
        borrowed.append(limiter.borrowed_tokens)
        return WS

    def note(*_args) -> NoteTarget:
        borrowed.append(limiter.borrowed_tokens)
        return NOTE

    resolver = MagicMock(spec=TargetResolver)
    resolver.workspace.side_effect = workspace
    resolver.note.side_effect = note
    return resolver


async def test_workspace_resolution_holds_a_limiter_slot():
    borrowed: list[int] = []
    target = await rest_deps.resolve_workspace_target("ws", USER, _resolver(borrowed))
    assert target == WS
    assert borrowed == [1]


async def test_write_workspace_resolution_holds_a_limiter_slot():
    borrowed: list[int] = []
    dep = rest_deps.RESOLVE_WORKSPACE_WRITE.dependency
    assert await dep("ws", USER, _resolver(borrowed)) == WS
    assert borrowed == [1]


async def test_note_resolution_holds_a_limiter_slot():
    borrowed: list[int] = []
    target = await rest_deps.resolve_note_target("ws", "n1", WS, _resolver(borrowed), USER)
    assert target == NOTE
    assert borrowed == [1]


async def test_write_note_resolution_holds_a_limiter_slot():
    borrowed: list[int] = []
    dep = rest_deps.RESOLVE_NOTE_WRITE.dependency
    assert await dep("ws", "n1", WS, _resolver(borrowed), USER) == NOTE
    assert borrowed == [1]
