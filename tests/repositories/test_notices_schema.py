from sqlalchemy import inspect

from kajet_turbo.db import Database


def test_notices_table_columns(database: Database):
    cols = {c["name"] for c in inspect(database.engine).get_columns("notices")}
    assert cols == {
        "id",
        "user_id",
        "kind",
        "key",
        "payload",
        "count",
        "first_seen_at",
        "last_seen_at",
        "resolved_at",
        "acknowledged_at",
        "last_delivered_at",
    }


def test_notices_unique_user_key(database: Database):
    ucs = inspect(database.engine).get_unique_constraints("notices")
    assert any(set(uc["column_names"]) == {"user_id", "key"} for uc in ucs)
