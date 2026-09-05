import time

import pytest

from kajet_turbo import perf
from kajet_turbo.db import Database
from kajet_turbo.log import setup_logging
from kajet_turbo.repositories import DbRepository
from tests.helpers import entries_named, read_log_entries


class _ExampleRepository(DbRepository):
    repository_name = "example"


def test_operation_records_local_and_aggregate_db_ms(database: Database, capsys):
    setup_logging()
    repo = _ExampleRepository(database.engine)

    with perf.perf_span() as span, repo.operation("write", item_id="i1"):
        pass

    assert span is not None
    assert "db_ms" in span.fields
    (entry,) = entries_named(read_log_entries(capsys), "repository_operation")
    assert entry["repository"] == "example"
    assert entry["operation"] == "example.write"
    assert entry["outcome"] == "success"
    assert entry["item_id"] == "i1"
    assert entry["db_ms"] >= 0


def test_operation_excludes_nested_work_from_local_and_aggregate_db_ms(database: Database, capsys):
    setup_logging()
    repo = _ExampleRepository(database.engine)

    with (
        perf.perf_span() as span,
        repo.operation("write") as operation,
        operation.session.begin(),
        perf.excluded_from("db_ms"),
    ):
        time.sleep(0.1)

    assert span is not None
    assert span.fields["db_ms"] < 50
    (entry,) = entries_named(read_log_entries(capsys), "repository_operation")
    assert entry["db_ms"] < 50


def test_operation_excludes_nested_work_from_local_db_ms_without_a_span(database: Database, capsys):
    setup_logging()
    repo = _ExampleRepository(database.engine)

    with (
        repo.operation("write") as operation,
        operation.session.begin(),
        perf.excluded_from("db_ms"),
    ):
        time.sleep(0.1)

    (entry,) = entries_named(read_log_entries(capsys), "repository_operation")
    assert entry["db_ms"] < 50


def test_operation_can_add_result_fields_and_suppress_noop(database: Database, capsys):
    setup_logging()
    repo = _ExampleRepository(database.engine)

    with repo.operation("write") as operation:
        operation.outcome = "created"
        operation.add_fields(count=2)
    with repo.operation("write") as operation:
        operation.suppress_log()

    (entry,) = entries_named(read_log_entries(capsys), "repository_operation")
    assert entry["outcome"] == "created"
    assert entry["count"] == 2


def test_operation_logs_error_type_without_error_text(database: Database, capsys):
    setup_logging()
    repo = _ExampleRepository(database.engine)

    with pytest.raises(RuntimeError, match="secret detail"), repo.operation("write"):
        raise RuntimeError("secret detail")

    (entry,) = entries_named(read_log_entries(capsys), "repository_operation")
    assert entry["outcome"] == "error"
    assert entry["error_type"] == "RuntimeError"
    assert "secret detail" not in str(entry)


def test_operation_rejects_reserved_fields(database: Database):
    repo = _ExampleRepository(database.engine)

    with pytest.raises(ValueError, match="reserved"), repo.operation("write", db_ms=1):
        pass


def test_timed_session_db_ms_unavailable_before_block_exits(database: Database):
    repo = _ExampleRepository(database.engine)
    timing = repo.timed_session()

    with pytest.raises(RuntimeError, match="only available after"):
        _ = timing.db_ms

    with timing, pytest.raises(RuntimeError, match="only available after"):
        _ = timing.db_ms

    assert timing.db_ms >= 0
