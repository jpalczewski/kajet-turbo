"""Parallel save/search/history on one workspace — catches git races,
SQLite pool exhaustion and (later) cache races under real threads."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from kajet_turbo.db import Database
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes import NoteService

WS = "stress"
OWNER = "user-stress"


@pytest.fixture()
def svc(tmp_path):
    from kajet_turbo.cache import WorkspaceCache

    db = Database(db_path=str(tmp_path / "stress.db"))
    service = NoteService(NoteRepository(db.engine), cache=WorkspaceCache())
    yield service, str(tmp_path / "ws")
    db.close()


def test_parallel_save_search_history(svc, tmp_path):
    service, ws_path = svc
    Path(ws_path).mkdir()
    GitRepository.init(ws_path)
    seed = service.save(OWNER, WS, ws_path, "Seed", "treść początkowa", [])

    errors: list[Exception] = []

    def save(i: int) -> None:
        try:
            service.save(OWNER, WS, ws_path, f"Nota {i}", f"treść {i}", ["tag"])
        except Exception as e:
            errors.append(e)

    def search(i: int) -> None:
        try:
            service.search("treść", [WS], owner_id=OWNER, limit=10)
        except Exception as e:
            errors.append(e)

    def history(i: int) -> None:
        try:
            service.get_history(seed["note_id"], owner_id=OWNER, ws_path=ws_path)
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
    notes = service.list(WS, owner_id=OWNER, limit=100)
    assert len(notes) == 21  # seed + 20 parallel saves
