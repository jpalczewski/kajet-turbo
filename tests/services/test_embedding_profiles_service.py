import pytest

from kajet_turbo.crypto import cipher_for
from kajet_turbo.embedding.base import EmbeddingAuthError, EmbeddingRequestRejected
from kajet_turbo.errors import EmbeddingProfileError
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository
from kajet_turbo.services.embedding_profiles import EmbeddingProfileService, ProbeFailedError
from tests.conftest import seed_user
from tests.helpers import entries_named, read_log_entries


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
    seed_user(database, "u1")
    svc, _ = _svc(database, dim=1024)
    out = svc.create_profile("u1", name="mmlw", base_url="http://h/v1", model="m", api_key="sk-x")
    assert out["dim"] == 1024
    assert out["is_active"] is True
    assert out["has_key"] is True
    assert "sk-x" not in str(out) and "api_key" not in out
    row = EmbeddingProfileRepository(database.engine).get("u1", out["id"])
    assert row is not None
    assert row.api_key_enc is not None
    assert cipher_for("embedding", secret="server-secret").decrypt(row.api_key_enc) == "sk-x"


def test_create_probe_failure_rejects(database):
    seed_user(database, "u1")
    svc, _ = _svc(database, probe_error=RuntimeError("401 from embedder"))
    with pytest.raises(ValueError):
        svc.create_profile("u1", name="bad", base_url="http://h/v1", model="m", api_key="sk-x")
    assert EmbeddingProfileRepository(database.engine).list_for_user("u1") == []


@pytest.mark.parametrize(
    ("error", "code", "logged"),
    [
        (
            EmbeddingAuthError("b", 401),
            EmbeddingProfileError.PROBE_AUTH_FAILED,
            {"status_code": 401},
        ),
        (
            EmbeddingRequestRejected("b", 400, "Invalid model specified"),
            EmbeddingProfileError.PROBE_REJECTED,
            {"status_code": 400, "detail": "Invalid model specified"},
        ),
        (RuntimeError("connection refused"), EmbeddingProfileError.PROBE_FAILED, {}),
    ],
)
def test_probe_failure_is_classified_and_logged(database, capsys, error, code, logged):
    from kajet_turbo.log import setup_logging

    setup_logging()
    seed_user(database, "u1")
    svc, _ = _svc(database, probe_error=error)
    capsys.readouterr()

    with pytest.raises(ProbeFailedError) as excinfo:
        svc.create_profile("u1", name="bad", base_url="http://h/v1", model="m-x", api_key="sk-x")

    assert excinfo.value.code == code
    (entry,) = entries_named(read_log_entries(capsys), "embedding_probe_failed")
    assert entry["level"] == "warning"
    assert entry["reason"] == code
    assert (entry["base_url"], entry["model"]) == ("http://h/v1", "m-x")
    assert logged.items() <= entry.items()
    assert "sk-x" not in str(entry)


def test_list_and_activate(database):
    seed_user(database, "u1")
    svc, _ = _svc(database)
    svc.create_profile("u1", "A", "http://a/v1", "m", "k")
    b = svc.create_profile("u1", "B", "http://b/v1", "m", "k")
    svc.activate_profile("u1", b["id"])
    listing = svc.list_profiles("u1")
    active = [p for p in listing if p["is_active"]]
    assert len(active) == 1 and active[0]["id"] == b["id"]
    assert all("api_key" not in p for p in listing)


def test_update_keeps_key_when_omitted(database):
    seed_user(database, "u1")
    svc, _ = _svc(database)
    p = svc.create_profile("u1", "A", "http://a/v1", "m", "sk-keep")
    svc.update_profile("u1", p["id"], name="A2", base_url="http://a/v1", model="m", api_key=None)
    row = EmbeddingProfileRepository(database.engine).get("u1", p["id"])
    assert row is not None
    assert row.api_key_enc is not None
    assert cipher_for("embedding", secret="server-secret").decrypt(row.api_key_enc) == "sk-keep"


def test_keyless_profile_create(database):
    seed_user(database, "u1")
    svc, _ = _svc(database)
    out = svc.create_profile("u1", "local", "http://local/v1", "m", None)
    assert out["has_key"] is False
    row = EmbeddingProfileRepository(database.engine).get("u1", out["id"])
    assert row is not None
    assert row.api_key_enc is None
