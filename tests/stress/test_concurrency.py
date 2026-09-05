"""Parallel save/search/history on one workspace — catches git races and
SQLite pool exhaustion under real threads."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget

WS = "stress"
OWNER = "user-stress"


def _ws(ws_path) -> WorkspaceTarget:
    return WorkspaceTarget(owner_id=OWNER, name=WS, path=Path(ws_path))


def _note(ws_path, note_id) -> NoteTarget:
    return NoteTarget(note_id=note_id, workspace=_ws(ws_path))


@pytest.fixture()
def svc(tmp_path, database_factory):
    from tests.services.conftest import build_note_service

    db = database_factory("stress.db")
    service = build_note_service(db)
    return service, str(tmp_path / "ws")


def test_parallel_save_search_history(svc, tmp_path):
    service, ws_path = svc
    Path(ws_path).mkdir()
    GitRepository.init(ws_path)
    seed = service.save(_ws(ws_path), "Seed", "treść początkowa", [])

    errors: list[Exception] = []

    def save(i: int) -> None:
        try:
            service.save(_ws(ws_path), f"Nota {i}", f"treść {i}", ["tag"])
        except Exception as e:
            errors.append(e)

    def search(i: int) -> None:
        try:
            service.search("treść", [WS], owner_id=OWNER, limit=10)
        except Exception as e:
            errors.append(e)

    def history(i: int) -> None:
        try:
            service.get_history(_note(ws_path, seed["note_id"]))
        except Exception as e:
            errors.append(e)

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = []
        for i in range(20):
            futures.append(ex.submit(save, i))
            futures.append(ex.submit(search, i))
            futures.append(ex.submit(history, i))
        for f in futures:
            f.result()

    assert errors == []
    notes = service.list_notes(_ws(ws_path), limit=100)
    assert len(notes) == 21  # seed + 20 parallel saves
