import pytest

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.markdown import Chunk
from kajet_turbo.repositories.notes import NoteChunkRepository, NoteRepository
from kajet_turbo.services.notes.related import NoteRelatedService
from kajet_turbo.workspace import InvalidFolderError
from tests.helpers import add_notes, related_note, vec_identity

CFG = EmbedderConfig(
    backend_id="http://test", type="openai", model="test-model", dim=2, base_url="http://test"
)

_note = related_note
_add_notes = add_notes


def _service(database, resolve_backend=lambda owner_id: CFG):
    chunk_repo = NoteChunkRepository(database.engine)
    note_repo = NoteRepository(database.engine)
    return NoteRelatedService(chunk_repo, note_repo, resolve_backend), chunk_repo


def test_related_returns_none_for_missing_note(database):
    service, _ = _service(database)
    assert service.related("nope", "u1", "ws") is None


def test_related_returns_none_for_wrong_owner(database):
    service, _ = _service(database)
    _add_notes(database, _note("src", owner_id="u1"))
    assert service.related("src", "someone-else", "ws") is None


def test_related_returns_none_for_wrong_workspace(database):
    service, _ = _service(database)
    _add_notes(database, _note("src", workspace="ws-a"))
    assert service.related("src", "u1", "ws-b") is None


def test_related_unavailable_when_no_backend(database):
    service, _ = _service(database, resolve_backend=lambda owner_id: None)
    _add_notes(database, _note("src"))
    result = service.related("src", "u1", "ws")
    assert result.state == "unavailable"
    assert result.items == []


def test_related_empty_when_note_has_no_chunks(database):
    service, _ = _service(database)
    _add_notes(database, _note("src"))
    result = service.related("src", "u1", "ws")
    assert result.state == "empty"
    assert result.items == []


def test_related_pending_when_chunks_unembedded(database):
    service, chunk_repo = _service(database)
    _add_notes(database, _note("src"))
    chunk_repo.replace_chunks("src", "ws", "u1", "src", [Chunk(0, ["# S"], "s", 0, 1)], None, None)
    result = service.related("src", "u1", "ws")
    assert result.state == "pending"
    assert result.source_chunks_total == 1
    assert result.items == []


def test_related_ready_hydrates_ranked_items(database):
    service, chunk_repo = _service(database)
    identity = vec_identity(2, model="test-model", backend="http://test")
    _add_notes(database, _note("src"), _note("near"), _note("far", folder="Other"))
    chunk_repo.ensure_vec_table(identity)
    chunk_repo.replace_chunks(
        "src", "ws", "u1", "src", [Chunk(0, ["# Src"], "s", 0, 1)], [[1.0, 0.0]], identity
    )
    chunk_repo.replace_chunks(
        "near", "ws", "u1", "near", [Chunk(0, ["# Near"], "n", 0, 1)], [[0.9, 0.1]], identity
    )
    chunk_repo.replace_chunks(
        "far", "ws", "u1", "far", [Chunk(0, ["# Far"], "f", 0, 1)], [[0.0, 1.0]], identity
    )

    result = service.related("src", "u1", "ws", limit=5)

    assert result.state == "ready"
    assert [item.note_id for item in result.items] == ["near", "far"]
    near = result.items[0]
    assert near.title == "near"
    assert near.folder == ""
    assert near.updated_at == "2026-01-01"
    assert near.source_chunk_id
    assert near.target_chunk_id
    assert near.source_header_path == ["# Src"]
    assert near.target_header_path == ["# Near"]
    assert near.target_content == "n"
    assert near.best_distance < result.items[1].best_distance


def test_related_ready_with_zero_items_when_nothing_else_indexed(database):
    service, chunk_repo = _service(database)
    identity = vec_identity(2, model="test-model", backend="http://test")
    _add_notes(database, _note("src"))
    chunk_repo.ensure_vec_table(identity)
    chunk_repo.replace_chunks(
        "src", "ws", "u1", "src", [Chunk(0, ["# S"], "s", 0, 1)], [[1.0, 0.0]], identity
    )
    result = service.related("src", "u1", "ws")
    assert result.state == "ready"
    assert result.items == []


def test_related_folder_scope_narrows_results(database):
    service, chunk_repo = _service(database)
    identity = vec_identity(2, model="test-model", backend="http://test")
    _add_notes(
        database,
        _note("src", folder="Journal"),
        _note("in_scope", folder="Journal/2026"),
        _note("out_of_scope", folder="Work"),
    )
    chunk_repo.ensure_vec_table(identity)
    chunk_repo.replace_chunks(
        "src", "ws", "u1", "src", [Chunk(0, ["# S"], "s", 0, 1)], [[1.0, 0.0]], identity
    )
    chunk_repo.replace_chunks(
        "in_scope", "ws", "u1", "in", [Chunk(0, ["# I"], "i", 0, 1)], [[0.9, 0.1]], identity
    )
    chunk_repo.replace_chunks(
        "out_of_scope", "ws", "u1", "out", [Chunk(0, ["# O"], "o", 0, 1)], [[0.9, 0.1]], identity
    )

    result = service.related("src", "u1", "ws", folder="Journal")

    assert [item.note_id for item in result.items] == ["in_scope"]


def _seed_folder_scope(database):
    service, chunk_repo = _service(database)
    identity = vec_identity(2, model="test-model", backend="http://test")
    _add_notes(
        database,
        _note("src", folder="Journal"),
        _note("nested", folder="Journal/2026"),
        _note("elsewhere", folder="Work"),
        _note("at_root", folder=""),
    )
    chunk_repo.ensure_vec_table(identity)
    for note_id, vector in [
        ("src", [1.0, 0.0]),
        ("nested", [0.9, 0.1]),
        ("elsewhere", [0.8, 0.2]),
        ("at_root", [0.7, 0.3]),
    ]:
        chunk_repo.replace_chunks(
            note_id, "ws", "u1", note_id, [Chunk(0, ["# H"], "h", 0, 1)], [vector], identity
        )
    return service


@pytest.mark.parametrize("folder", ["", "/", "  "])
def test_related_root_folder_scopes_to_whole_workspace(database, folder):
    service = _seed_folder_scope(database)

    result = service.related("src", "u1", "ws", folder=folder)

    assert {item.note_id for item in result.items} == {"nested", "elsewhere", "at_root"}


@pytest.mark.parametrize("folder", ["Journal/", "/Journal", "Journal//"])
def test_related_folder_is_normalized(database, folder):
    service = _seed_folder_scope(database)

    result = service.related("src", "u1", "ws", folder=folder)

    assert [item.note_id for item in result.items] == ["nested"]


def test_related_rejects_folder_escaping_workspace(database):
    service = _seed_folder_scope(database)
    with pytest.raises(InvalidFolderError):
        service.related("src", "u1", "ws", folder="../etc")


@pytest.mark.parametrize("limit", [0, -1, 51, 100])
def test_related_rejects_out_of_range_limit(database, limit):
    service, _ = _service(database)
    _add_notes(database, _note("src"))
    with pytest.raises(ValueError, match="limit"):
        service.related("src", "u1", "ws", limit=limit)


@pytest.mark.parametrize("limit", [1, 50])
def test_related_accepts_boundary_limits(database, limit):
    service, _ = _service(database)
    _add_notes(database, _note("src"))
    result = service.related("src", "u1", "ws", limit=limit)
    assert result.state == "empty"  # no chunks — limit validation alone is under test


def test_related_never_calls_embedder_or_touches_jobs(database):
    from kajet_turbo.repositories.jobs import JobRepository

    calls = {"resolve": 0}

    def _resolve(owner_id):
        calls["resolve"] += 1
        return CFG

    service, chunk_repo = _service(database, resolve_backend=_resolve)
    identity = vec_identity(2, model="test-model", backend="http://test")
    _add_notes(database, _note("src"), _note("near"))
    chunk_repo.ensure_vec_table(identity)
    chunk_repo.replace_chunks(
        "src", "ws", "u1", "src", [Chunk(0, ["# S"], "s", 0, 1)], [[1.0, 0.0]], identity
    )
    chunk_repo.replace_chunks(
        "near", "ws", "u1", "near", [Chunk(0, ["# N"], "n", 0, 1)], [[0.9, 0.1]], identity
    )

    jobs = JobRepository(database.engine)
    assert jobs.list_jobs("u1") == []  # sanity: nothing queued before the call

    result = service.related("src", "u1", "ws")

    assert result.state == "ready"
    # resolve_backend is used only to derive the identity — never to build/call an
    # embedder — and the call itself is not what enqueues a job.
    assert calls["resolve"] == 1
    assert jobs.list_jobs("u1") == []


async def test_related_async_matches_sync_result(database):
    service, chunk_repo = _service(database)
    identity = vec_identity(2, model="test-model", backend="http://test")
    _add_notes(database, _note("src"), _note("near"))
    chunk_repo.ensure_vec_table(identity)
    chunk_repo.replace_chunks(
        "src", "ws", "u1", "src", [Chunk(0, ["# S"], "s", 0, 1)], [[1.0, 0.0]], identity
    )
    chunk_repo.replace_chunks(
        "near", "ws", "u1", "near", [Chunk(0, ["# N"], "n", 0, 1)], [[0.9, 0.1]], identity
    )

    result = await service.related_async("src", "u1", "ws")

    assert result.state == "ready"
    assert [item.note_id for item in result.items] == ["near"]
