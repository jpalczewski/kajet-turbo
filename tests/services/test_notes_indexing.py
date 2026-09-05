from kajet_turbo.markdown import EditSpec
from kajet_turbo.repositories.notes import NoteChunkRepository
from tests.services.conftest import build_note_service, note_target, workspace_target


class _RecordingIndexer:
    def __init__(self):
        self.indexed = []

    def index_note(self, note_id, workspace, owner_id, title, content, **_kwargs):
        self.indexed.append((note_id, title, content))


def test_save_triggers_indexing(database, git_workspace_factory):
    indexer = _RecordingIndexer()
    service = build_note_service(database, indexer=indexer)
    ws = git_workspace_factory("ws")
    service.save(workspace_target("u1", "ws", ws), "Title", "# Title\n\nbody\n", tags=[])
    assert len(indexer.indexed) == 1
    assert indexer.indexed[0][1] == "Title"
    assert "body" in indexer.indexed[0][2]


def test_save_writes_fts_via_indexer(database, git_workspace_factory):
    from sqlalchemy import text

    from kajet_turbo.embedding.cache import EmbeddingCacheRepository
    from kajet_turbo.repositories.jobs import JobRepository
    from kajet_turbo.services.indexing import NoteIndexer

    chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda o: None,
        jobs=JobRepository(database.engine),
    )
    service = build_note_service(database, indexer=indexer)
    ws = git_workspace_factory("ws")
    service.save(workspace_target("u1", "ws", ws), "Title", "# Title\n\nsearchable body\n", tags=[])
    with database.engine.connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(*) FROM notes_fts WHERE notes_fts MATCH 'searchable'")
        ).scalar()
    assert n >= 1


def test_save_surfaces_chunk_write_failure(database, git_workspace_factory):
    import pytest

    class _BadIndexer:
        def index_note(self, *a, **k):
            raise RuntimeError("DB exploded")

    service = build_note_service(database, indexer=_BadIndexer())
    ws = git_workspace_factory("ws")
    with pytest.raises(RuntimeError):
        service.save(workspace_target("u1", "ws", ws), "Title", "# Title\n\nbody\n", tags=[])


def test_update_reindexes_with_new_content(database, git_workspace_factory):
    indexer = _RecordingIndexer()
    service = build_note_service(database, indexer=indexer)
    ws = git_workspace_factory("ws")
    res = service.save(workspace_target("u1", "ws", ws), "Title", "# Title\n\nold\n", tags=[])
    target = note_target("u1", "ws", ws, res["note_id"])
    sha = service.get_history(target)[0]["sha"]
    service.update(
        target,
        expected_sha=sha,
        edit=EditSpec(content="# Title\n\nbrand new body\n"),
    )
    assert any("brand new body" in c for _, _, c in indexer.indexed)


def test_delete_does_not_require_indexer_cleanup(database, git_workspace_factory):
    indexer = _RecordingIndexer()
    service = build_note_service(database, indexer=indexer)
    ws = git_workspace_factory("ws")
    res = service.save(workspace_target("u1", "ws", ws), "Title", "# Title\n\nbody\n", tags=[])
    service.delete(note_target("u1", "ws", ws, res["note_id"]))
    assert service._crud_repo.get(res["note_id"], owner_id="u1") is None


def test_no_indexer_is_a_noop(database, git_workspace_factory):
    service = build_note_service(database)  # no indexer
    ws = git_workspace_factory("ws")
    res = service.save(workspace_target("u1", "ws", ws), "Title", "# Title\n\nbody\n", tags=[])
    # must not raise; delete must not raise either
    service.delete(note_target("u1", "ws", ws, res["note_id"]))
    assert "note_id" in res
