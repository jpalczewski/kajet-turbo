import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import ClassVar

from sqlalchemy import Engine
from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.perf import local_exclusion_scope, timed

_LOG_RESERVED_FIELDS = {"msg", "repository", "operation", "outcome", "db_ms"}
_last_db_timing: ContextVar[tuple[int, float] | None] = ContextVar(
    "repository_last_db_timing", default=None
)


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


class DbRepository:
    """Base for all SQLModel repositories. Provides engine storage and a
    combined Session+timed context manager so subclasses don't repeat boilerplate."""

    repository_name: ClassVar[str] = ""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _require_repository_name(self) -> None:
        if not self.repository_name:
            raise TypeError(f"{type(self).__name__} must define repository_name")

    @contextmanager
    def timed_session(self) -> Generator[Session]:
        _last_db_timing.set(None)
        started = time.monotonic()
        try:
            with (
                local_exclusion_scope() as pop_excluded,
                Session(self._engine) as session,
                timed("db_ms"),
            ):
                yield session
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            _last_db_timing.set((id(self), round(elapsed_ms - pop_excluded("db_ms"), 1)))

    def _last_operation_db_ms(self) -> float:
        """The ``db_ms`` a just-exited ``timed_session()`` call recorded for this repo."""
        timing = _last_db_timing.get()
        assert timing is not None and timing[0] == id(self)
        return timing[1]

    def log_operation(self, action: str, *, outcome: str = "success", **fields: object) -> None:
        """Log an already-completed ``timed_session`` using the common schema."""
        timing = _last_db_timing.get()
        if timing is None or timing[0] != id(self):
            raise RuntimeError("log_operation() requires a completed timed_session()")
        self._emit_operation(action, outcome=outcome, db_ms=timing[1], fields=fields)

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
        try:
            with self.timed_session() as session:
                operation = DbOperation(session=session, fields=captured_fields)
                yield operation
        except Exception as exc:
            self._emit_operation(
                action,
                outcome="error",
                db_ms=self._last_operation_db_ms(),
                fields={**captured_fields, "error_type": type(exc).__name__},
                level="WARNING",
            )
            raise

        if operation.emit:
            self._emit_operation(
                action,
                outcome=operation.outcome,
                db_ms=self._last_operation_db_ms(),
                fields=operation.fields,
            )
