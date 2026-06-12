from collections.abc import Callable
from pathlib import Path

import pytest

from kajet_turbo.db import Database
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes import NoteService


@pytest.fixture
def workspace(git_workspace_factory: Callable[[str], Path]) -> Path:
    return git_workspace_factory("workspace")


@pytest.fixture
def service(database: Database) -> NoteService:
    return NoteService(NoteRepository(database.engine))
