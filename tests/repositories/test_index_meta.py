from kajet_turbo.repositories.notes import NoteChunkRepository, NoteRepository


def test_index_meta_upsert_and_get(database):
    repo = NoteChunkRepository(database.engine)
    assert repo.get_index_meta("u1") is None
    repo.upsert_index_meta("u1", backend="openai-large", model="text-embedding-3-large", dim=3072)
    meta = repo.get_index_meta("u1")
    assert meta is not None
    assert (meta["backend"], meta["model"], meta["dim"]) == (
        "openai-large",
        "text-embedding-3-large",
        3072,
    )

    repo.upsert_index_meta("u1", backend="mmlw", model="mmlw", dim=1024)
    meta = repo.get_index_meta("u1")
    assert meta is not None
    assert (meta["backend"], meta["model"], meta["dim"]) == ("mmlw", "mmlw", 1024)


def test_dead_vec_methods_stay_removed():
    # Removed in Task 1; guard against accidental reintroduction.
    repo_attrs = dir(NoteRepository)
    assert "insert_vec" not in repo_attrs
    assert "search_vec" not in repo_attrs
    assert "has_vec_index" not in repo_attrs
