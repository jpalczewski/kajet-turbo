from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlmodel import Session

from kajet_turbo.perf import timed


class DbRepository:
    """Base for all SQLModel repositories. Provides engine storage and a
    combined Session+timed context manager so subclasses don't repeat boilerplate."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def timed_session(self) -> Generator[Session]:
        with Session(self._engine) as session, timed("db_ms"):
            yield session
