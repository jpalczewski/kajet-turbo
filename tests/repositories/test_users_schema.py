from sqlalchemy import inspect, text

from kajet_turbo.db import Database


def test_users_table_columns(database: Database):
    cols = {c["name"] for c in inspect(database.engine).get_columns("users")}
    assert {"id", "email", "password_hash", "created_at", "timezone", "locale"} <= cols


def test_users_preferences_backfill_via_server_default(database: Database):
    """Inserting a row without timezone/locale (as pre-migration rows would have been)
    must still get the migration's server_default, proving the DB-level backfill —
    not just the SQLModel/ORM Python-side default."""
    with database.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id, email, created_at) VALUES (:id, :email, :created_at)"),
            {"id": "legacy-user", "email": "legacy@example.com", "created_at": "2026-01-01"},
        )
        row = conn.execute(
            text("SELECT timezone, locale FROM users WHERE id = :id"), {"id": "legacy-user"}
        ).fetchone()
    assert row is not None
    assert row.timezone == "Europe/Warsaw"
    assert row.locale == "pl"
