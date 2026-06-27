from sqlmodel import Session

from kajet_turbo.models import User
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository


def _user(database, uid="u1"):
    with Session(database.engine) as s:
        s.add(User(id=uid, email=f"{uid}@e.com", created_at="2026-01-01"))
        s.commit()


def test_create_list_get(database):
    _user(database)
    repo = EmbeddingProfileRepository(database.engine)
    p = repo.create(
        "u1",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        model="text-embedding-3-large",
        api_key_enc=b"sealed",
        dim=3072,
    )
    assert p.id and p.is_active is True  # first profile auto-activates
    rows = repo.list_for_user("u1")
    assert [r.name for r in rows] == ["OpenAI"]
    got = repo.get("u1", p.id)
    assert got is not None
    assert got.model == "text-embedding-3-large"


def test_second_profile_not_auto_active_and_activate_switches(database):
    _user(database)
    repo = EmbeddingProfileRepository(database.engine)
    a = repo.create("u1", "A", "http://a/v1", "m", b"k", 3)
    b = repo.create("u1", "B", "http://b/v1", "m", b"k", 4)
    assert a.is_active is True and b.is_active is False
    repo.set_active("u1", b.id)
    active = repo.get_active("u1")
    assert active is not None
    assert active.id == b.id
    inactive = repo.get("u1", a.id)
    assert inactive is not None
    assert inactive.is_active is False  # exactly one active


def test_update_and_delete(database):
    _user(database)
    repo = EmbeddingProfileRepository(database.engine)
    p = repo.create("u1", "A", "http://a/v1", "m", b"k", 3)
    repo.update(
        "u1", p.id, name="A2", base_url="http://a2/v1", model="m2", api_key_enc=b"k2", dim=5
    )
    g = repo.get("u1", p.id)
    assert g is not None
    assert (g.name, g.base_url, g.model, g.dim) == ("A2", "http://a2/v1", "m2", 5)
    repo.delete("u1", p.id)
    assert repo.get("u1", p.id) is None


def test_delete_active_promotes_another(database):
    _user(database)
    repo = EmbeddingProfileRepository(database.engine)
    a = repo.create("u1", "A", "http://a/v1", "m", b"k", 3)
    b = repo.create("u1", "B", "http://b/v1", "m", b"k", 4)
    repo.set_active("u1", a.id)
    repo.delete("u1", a.id)  # deleting the active one should promote a remaining profile
    active = repo.get_active("u1")
    assert active is not None
    assert active.id == b.id


def test_get_active_none_when_no_profiles(database):
    _user(database)
    assert EmbeddingProfileRepository(database.engine).get_active("u1") is None


def test_owner_scoped(database):
    _user(database, "u1")
    _user(database, "u2")
    repo = EmbeddingProfileRepository(database.engine)
    p = repo.create("u1", "A", "http://a/v1", "m", b"k", 3)
    assert repo.get("u2", p.id) is None  # cannot read another user's profile
    assert repo.list_for_user("u2") == []
