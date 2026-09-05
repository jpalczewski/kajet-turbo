import time
from collections.abc import Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field
from types import TracebackType
from typing import ClassVar

from sqlalchemy import CursorResult, Engine, Executable
from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.perf import local_exclusion_scope, timed

_LOG_RESERVED_FIELDS = {"msg", "repository", "operation", "outcome", "db_ms"}


def _check_reserved(fields: dict[str, object]) -> None:
    reserved = _LOG_RESERVED_FIELDS.intersection(fields)
    if reserved:
        names = ", ".join(sorted(reserved))
        raise ValueError(f"Repository log fields are reserved: {names}")


@dataclass(slots=True)
class DbOperation:
    """One observable repository transaction.

    Callers may attach result fields before the context closes or suppress a log for a
    no-op.  The session itself stays explicit, which keeps transaction boundaries easy
    to see and lets cross-table operations share one commit.
    """

    session: Session
    outcome: str = "success"
    emit: bool = True
    fields: dict[str, object] = field(default_factory=dict)

    def add_fields(self, **fields: object) -> None:
        _check_reserved(fields)
        self.fields.update(fields)

    def suppress_log(self) -> None:
        self.emit = False

    def report_count(self, count: int) -> None:
        """Attach ``count`` when it is non-zero; suppress the log entirely for a no-op."""
        if count:
            self.add_fields(count=count)
        else:
            self.suppress_log()


class TimedSession(AbstractContextManager[Session]):
    """A ``Session`` context manager that exposes its own elapsed time once closed.

    Most callers just do ``with self.timed_session() as session: ...`` and never touch
    the timing. A caller that needs it for a deferred ``log_operation()`` call holds
    onto the instance instead of only its yielded session::

        timing = self.timed_session()
        with timing as session:
            ...
        self.log_operation("action", timing.db_ms, ...)

    ``db_ms`` raises until the ``with`` block exits, so there is no way to read a stale
    or zeroed timing by calling too early — the value only exists once it is real.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._db_ms: float | None = None

    @property
    def db_ms(self) -> float:
        if self._db_ms is None:
            raise RuntimeError("db_ms is only available after the timed_session block exits")
        return self._db_ms

    def __enter__(self) -> Session:
        self._started = time.monotonic()
        with ExitStack() as stack:
            pop_excluded = stack.enter_context(local_exclusion_scope())
            session = stack.enter_context(Session(self._engine))
            stack.enter_context(timed("db_ms"))
            self._pop_excluded = pop_excluded
            self._stack = stack.pop_all()
        return session

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        try:
            return self._stack.__exit__(exc_type, exc, tb)
        finally:
            elapsed_ms = (time.monotonic() - self._started) * 1000
            self._db_ms = round(elapsed_ms - self._pop_excluded("db_ms"), 1)


class DbRepository:
    """Base for all SQLModel repositories. Provides engine storage and a
    combined Session+timed context manager so subclasses don't repeat boilerplate."""

    repository_name: ClassVar[str] = ""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _require_repository_name(self) -> None:
        if not self.repository_name:
            raise TypeError(f"{type(self).__name__} must define repository_name")

    def timed_session(self) -> TimedSession:
        return TimedSession(self._engine)

    @staticmethod
    def _raw_execute(
        session: Session, stmt: Executable, params: dict[str, object] | None = None
    ) -> CursorResult:
        """session.exec() can't type a text() statement; centralizes the fallback.

        Static, not bound to a live repository, so the ``_in_session`` staticmethods that
        take ``session`` as an explicit parameter can call it the same way instance methods
        do.
        """
        result = session.execute(stmt, params)  # ty: ignore[deprecated] - raw SQL
        assert isinstance(result, CursorResult)
        return result

    def log_operation(
        self, action: str, db_ms: float, *, outcome: str = "success", **fields: object
    ) -> None:
        """Log a completed ``timed_session()``.

        ``db_ms`` is that session's ``TimedSession.db_ms``, passed explicitly by the
        caller — see ``TimedSession`` for the pattern that produces it.
        """
        self._emit_operation(action, outcome=outcome, db_ms=db_ms, fields=fields)

    def _emit_operation(
        self,
        action: str,
        *,
        outcome: str,
        db_ms: float,
        fields: dict[str, object],
        level: str = "INFO",
    ) -> None:
        self._require_repository_name()
        _check_reserved(fields)
        logger.log(
            level,
            "repository_operation",
            repository=self.repository_name,
            operation=f"{self.repository_name}.{action}",
            outcome=outcome,
            db_ms=db_ms,
            **fields,
        )

    @contextmanager
    def operation(self, action: str, **fields: object) -> Generator[DbOperation]:
        """Run and log one repository operation with a local and aggregate DB timing.

        Unlike ``timed()``, the local measurement is always active.  This makes worker
        repository logs independently profileable even when there is no surrounding
        HTTP/MCP performance span.  When a span exists, ``timed_session`` also adds the
        same transaction to its aggregate ``db_ms`` field.
        """
        self._require_repository_name()
        _check_reserved(fields)

        # Shared with DbOperation.fields (not copied again) so add_fields() calls before an
        # exception are still visible to the except-branch log below.
        captured_fields = dict(fields)
        timing = self.timed_session()
        try:
            with timing as session:
                operation = DbOperation(session=session, fields=captured_fields)
                yield operation
        except Exception as exc:
            self._emit_operation(
                action,
                outcome="error",
                db_ms=timing.db_ms,
                fields={**captured_fields, "error_type": type(exc).__name__},
                level="WARNING",
            )
            raise

        if operation.emit:
            self._emit_operation(
                action,
                outcome=operation.outcome,
                db_ms=timing.db_ms,
                fields=operation.fields,
            )
