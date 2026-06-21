from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes import NoteService


class _RecordingIndexer:
    def __init__(self):
        self.indexed = []
        self.cleared = []

    def index_note(self, note_id, workspace, owner_id, title, content):
        self.indexed.append((note_id, title, content))

    def clear_note(self, note_id):
        self.cleared.append(note_id)


def test_save_triggers_indexing(database, git_workspace_factory):
    indexer = _RecordingIndexer()
    service = NoteService(NoteRepository(database.engine), indexer=indexer)
    ws = git_workspace_factory("ws")
    service.save("u1", "ws", str(ws), "Title", "# Title\n\nbody\n", tags=[])
    assert len(indexer.indexed) == 1
    assert indexer.indexed[0][1] == "Title"
    assert "body" in indexer.indexed[0][2]


def test_save_writes_fts_via_indexer(database, git_workspace_factory):
    from sqlalchemy import text

    from kajet_turbo.embedding.cache import EmbeddingCacheRepository
    from kajet_turbo.services.indexing import NoteIndexer

    repo = NoteRepository(database.engine)
    indexer = NoteIndexer(
        repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda o: None,
        build_embedder=lambda c: None,
    )
    service = NoteService(repo, indexer=indexer)
    ws = git_workspace_factory("ws")
    service.save("u1", "ws", str(ws), "Title", "# Title\n\nsearchable body\n", tags=[])
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

        def clear_note(self, *a, **k):
            pass

    service = NoteService(NoteRepository(database.engine), indexer=_BadIndexer())
    ws = git_workspace_factory("ws")
    with pytest.raises(RuntimeError):
        service.save("u1", "ws", str(ws), "Title", "# Title\n\nbody\n", tags=[])


def test_update_reindexes_with_new_content(database, git_workspace_factory):
    indexer = _RecordingIndexer()
    service = NoteService(NoteRepository(database.engine), indexer=indexer)
    ws = git_workspace_factory("ws")
    res = service.save("u1", "ws", str(ws), "Title", "# Title\n\nold\n", tags=[])
    service.update(
        res["note_id"],
        owner_id="u1",
        ws_path=str(ws),
        content="# Title\n\nbrand new body\n",
        confirm=True,
    )
    assert any("brand new body" in c for _, _, c in indexer.indexed)


def test_delete_clears_chunks_before_row_delete(database, git_workspace_factory):
    indexer = _RecordingIndexer()
    service = NoteService(NoteRepository(database.engine), indexer=indexer)
    ws = git_workspace_factory("ws")
    res = service.save("u1", "ws", str(ws), "Title", "# Title\n\nbody\n", tags=[])
    service.delete(res["note_id"], owner_id="u1", ws_path=str(ws))
    assert res["note_id"] in indexer.cleared


def test_no_indexer_is_a_noop(database, git_workspace_factory):
    service = NoteService(NoteRepository(database.engine))  # no indexer
    ws = git_workspace_factory("ws")
    res = service.save("u1", "ws", str(ws), "Title", "# Title\n\nbody\n", tags=[])
    # must not raise; delete must not raise either
    service.delete(res["note_id"], owner_id="u1", ws_path=str(ws))
    assert "note_id" in res
