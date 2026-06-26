import pytest
from sqlmodel import Session

from kajet_turbo.crypto import cipher_for
from kajet_turbo.models import User
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository
from kajet_turbo.services.embedding_profiles import EmbeddingProfileService


def _user(database, uid="u1"):
    with Session(database.engine) as s:
        s.add(User(id=uid, email=f"{uid}@e.com", created_at="2026-01-01"))
        s.commit()


def _svc(database, *, dim=3, probe_error=None):
    _dim_holder = {"dim": dim}

    def probe_embed(base_url, model, api_key):
        if probe_error:
            raise probe_error
        return _dim_holder["dim"]

    return EmbeddingProfileService(
        EmbeddingProfileRepository(database.engine),
        cipher_factory=lambda: cipher_for("embedding", secret="server-secret"),
        probe_dim=probe_embed,
    ), _dim_holder


def test_create_probes_dim_and_seals_key(database):
    _user(database)
    svc, _ = _svc(database, dim=1024)
    out = svc.create_profile("u1", name="mmlw", base_url="http://h/v1", model="m", api_key="sk-x")
    assert out["dim"] == 1024
    assert out["is_active"] is True
    assert out["has_key"] is True
    assert "sk-x" not in str(out) and "api_key" not in out
    row = EmbeddingProfileRepository(database.engine).get("u1", out["id"])
    assert cipher_for("embedding", secret="server-secret").decrypt(row.api_key_enc) == "sk-x"


def test_create_probe_failure_rejects(database):
    _user(database)
    svc, _ = _svc(database, probe_error=RuntimeError("401 from embedder"))
    with pytest.raises(ValueError):
        svc.create_profile("u1", name="bad", base_url="http://h/v1", model="m", api_key="sk-x")
    assert EmbeddingProfileRepository(database.engine).list_for_user("u1") == []


def test_list_and_activate(database):
    _user(database)
    svc, _ = _svc(database)
    svc.create_profile("u1", "A", "http://a/v1", "m", "k")
    b = svc.create_profile("u1", "B", "http://b/v1", "m", "k")
    svc.activate_profile("u1", b["id"])
    listing = svc.list_profiles("u1")
    active = [p for p in listing if p["is_active"]]
    assert len(active) == 1 and active[0]["id"] == b["id"]
    assert all("api_key" not in p for p in listing)


def test_update_keeps_key_when_omitted(database):
    _user(database)
    svc, _ = _svc(database)
    p = svc.create_profile("u1", "A", "http://a/v1", "m", "sk-keep")
    svc.update_profile("u1", p["id"], name="A2", base_url="http://a/v1", model="m", api_key=None)
    row = EmbeddingProfileRepository(database.engine).get("u1", p["id"])
    assert cipher_for("embedding", secret="server-secret").decrypt(row.api_key_enc) == "sk-keep"


def test_keyless_profile_create(database):
    _user(database)
    svc, _ = _svc(database)
    out = svc.create_profile("u1", "local", "http://local/v1", "m", None)
    assert out["has_key"] is False
    assert EmbeddingProfileRepository(database.engine).get("u1", out["id"]).api_key_enc is None
