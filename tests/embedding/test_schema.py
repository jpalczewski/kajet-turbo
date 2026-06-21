from sqlalchemy import text


def test_embedding_tables_exist(database):
    with database.engine.connect() as conn:
        names = {
            r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    assert "embedding_cache" in names
    assert "user_embedding_config" in names


def test_embedding_cache_columns(database):
    with database.engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(embedding_cache)"))}
    assert {
        "content_hash",
        "backend",
        "model",
        "dim",
        "embedding",
        "created_at",
        "last_used_at",
    } <= cols


def test_user_embedding_config_columns(database):
    with database.engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(user_embedding_config)"))}
    assert {"user_id", "backend_id", "api_key_enc", "created_at", "updated_at"} <= cols
