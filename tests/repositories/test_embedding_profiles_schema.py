from sqlalchemy import text

_TABLES_SQL = "SELECT name FROM sqlite_master WHERE type='table'"


def test_embedding_profiles_table(database):
    with database.engine.connect() as conn:
        names = {r[0] for r in conn.execute(text(_TABLES_SQL))}
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(embedding_profiles)"))}
    assert "embedding_profiles" in names
    assert {
        "id",
        "user_id",
        "name",
        "base_url",
        "model",
        "api_key_enc",
        "dim",
        "is_active",
        "created_at",
        "updated_at",
    } <= cols


def test_user_embedding_config_gone(database):
    with database.engine.connect() as conn:
        names = {r[0] for r in conn.execute(text(_TABLES_SQL))}
    assert "user_embedding_config" not in names
