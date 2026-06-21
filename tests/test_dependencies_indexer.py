def test_note_service_has_indexer_wired():
    from kajet_turbo import dependencies

    assert getattr(dependencies.note_service, "_indexer", None) is not None
