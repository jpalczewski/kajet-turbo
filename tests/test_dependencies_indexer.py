def test_note_service_has_indexer_wired(tmp_path):
    from kajet_turbo.dependencies import AppConfig, build_resources

    resources = build_resources(
        AppConfig(db_path=str(tmp_path / "test.db"), mcp_base_url="http://localhost")
    )
    try:
        assert getattr(resources.note_service, "_indexer", None) is not None
    finally:
        resources.db.close()
