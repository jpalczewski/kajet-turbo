from sqlmodel import Session

from kajet_turbo.models import User
from kajet_turbo.repositories.embedding_config import EmbeddingConfigRepository


def _make_user(database, user_id="u1"):
    with Session(database.engine) as session:
        session.add(User(id=user_id, email=f"{user_id}@example.com", created_at="2026-01-01"))
        session.commit()


def test_get_missing_returns_none(database):
    repo = EmbeddingConfigRepository(database.engine)
    assert repo.get("nobody") is None


def test_upsert_insert_then_get(database):
    _make_user(database)
    repo = EmbeddingConfigRepository(database.engine)
    repo.upsert("u1", backend_id="openai-large", api_key_enc=b"sealed")

    row = repo.get("u1")
    assert row is not None
    assert row.backend_id == "openai-large"
    assert row.api_key_enc == b"sealed"
    assert row.created_at == row.updated_at


def test_upsert_update_changes_fields_and_updated_at(database):
    _make_user(database)
    repo = EmbeddingConfigRepository(database.engine)
    repo.upsert("u1", backend_id="openai-large", api_key_enc=b"old")
    created = repo.get("u1").created_at

    repo.upsert("u1", backend_id="mmlw", api_key_enc=b"new")
    row = repo.get("u1")
    assert row.backend_id == "mmlw"
    assert row.api_key_enc == b"new"
    assert row.created_at == created  # created_at preserved on update


def test_upsert_allows_null_backend_and_key(database):
    _make_user(database)
    repo = EmbeddingConfigRepository(database.engine)
    repo.upsert("u1", backend_id=None, api_key_enc=None)
    row = repo.get("u1")
    assert row.backend_id is None
    assert row.api_key_enc is None
