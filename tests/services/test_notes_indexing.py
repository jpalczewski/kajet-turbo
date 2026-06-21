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


def test_save_succeeds_even_if_indexer_raises(database, git_workspace_factory):
    class _Boom:
        def index_note(self, *a, **k):
            raise RuntimeError("indexing blew up")

        def clear_note(self, *a, **k):
            raise RuntimeError("clear blew up")

    service = NoteService(NoteRepository(database.engine), indexer=_Boom())
    ws = git_workspace_factory("ws")
    result = service.save("u1", "ws", str(ws), "Title", "# Title\n\nbody\n", tags=[])
    assert "note_id" in result


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
