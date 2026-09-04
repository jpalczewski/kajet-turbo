"""set_tags/update confirmation gating and stale expected_sha coverage."""

from kajet_turbo.markdown import EditSpec


def test_set_tags_stale_sha_rejected(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "T", "body", ["docs", "extra"])["note_id"]
    result = service.set_tags(note_id, "u1", str(workspace), ["docs"], expected_sha="0" * 12)
    assert result["stale_sha"] is True
    # unchanged on disk/index
    assert sorted(
        service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace)).tags
    ) == ["docs", "extra"]


def test_set_tags_fresh_sha_applies_drop(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "T2", "body", ["docs", "extra"])["note_id"]
    sha = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace)).sha
    result = service.set_tags(note_id, "u1", str(workspace), ["docs"], expected_sha=sha)
    assert result["frontmatter_tags"] == ["docs"]


def test_set_tags_none_sha_skips_check(service, workspace):
    """REST API path: expected_sha=None is a trusted caller — no gate."""
    note_id = service.save("u1", "ws", str(workspace), "T3", "body", ["docs", "extra"])["note_id"]
    result = service.set_tags(note_id, "u1", str(workspace), ["docs"])
    assert result["frontmatter_tags"] == ["docs"]


def test_update_fresh_sha_applies_content_overwrite(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "stara treść", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    result = service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        edit=EditSpec(content="nowa treść"),
    )

    assert result == {
        "note_id": note_id,
        "replaced": None,
        "warnings": [],
        "temporal_warnings": [],
        "occurred_at": None,
        "period": None,
    }
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "nowa treść"


def test_update_no_gate_on_empty_body_overwrite(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    result = service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        edit=EditSpec(content="pierwsza treść"),
    )

    assert result == {
        "note_id": note_id,
        "replaced": None,
        "warnings": [],
        "temporal_warnings": [],
        "occurred_at": None,
        "period": None,
    }
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "pierwsza treść"


def test_update_no_gate_on_surgical_append(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "## H\n\n- a", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    result = service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        edit=EditSpec(content="- b", mode="append", target_heading="## H"),
    )

    assert result == {
        "note_id": note_id,
        "replaced": None,
        "warnings": [],
        "temporal_warnings": [],
        "occurred_at": None,
        "period": None,
    }


def test_update_fresh_sha_applies_tag_drop(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python", "work"])[
        "note_id"
    ]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, tags=["python"]
    )

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.tags == ["python"]


def test_update_rejects_stale_expected_sha(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "v1", [])["note_id"]
    stale_sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=stale_sha,
        edit=EditSpec(content="v2"),
    )

    result = service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=stale_sha,
        edit=EditSpec(content="v3"),
    )

    assert result["stale_sha"] is True
    assert "current_sha" not in result
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "v2"


def test_delete_stale_sha_rejected(service, workspace):
    note_id = service.save("u1", "test-ws", str(workspace), "Del", "body", [])["note_id"]
    result = service.delete(note_id, owner_id="u1", ws_path=str(workspace), expected_sha="0" * 12)
    assert result["stale_sha"] is True
    assert service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace)) is not None


def test_delete_none_sha_skips_check(service, workspace):
    note_id = service.save("u1", "test-ws", str(workspace), "Del2", "body", [])["note_id"]
    result = service.delete(note_id, owner_id="u1", ws_path=str(workspace))
    assert result == {"note_id": note_id}


def test_delete_missing_file_skips_sha_check(service, workspace):
    """Orphaned DB row (file deleted out-of-band): sha check is skipped, delete proceeds
    as pure index/DB cleanup — there is no version the caller could have read."""
    note_id = service.save("u1", "test-ws", str(workspace), "Del3", "body", [])["note_id"]
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    (workspace / note.title).with_suffix(".md").unlink()

    result = service.delete(note_id, owner_id="u1", ws_path=str(workspace), expected_sha="0" * 12)

    assert result == {"note_id": note_id}
    assert service.get(note_id, owner_id="u1") is None


def test_update_stale_sha_rejected_even_for_pure_append(service, workspace):
    # Zero data-loss risk (surgical append), but staleness gate is independent of intent.
    note_id = service.save("u1", "ws", str(workspace), "Notka", "## H\n\n- a", [])["note_id"]
    stale_sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=stale_sha,
        edit=EditSpec(content="- b", mode="append", target_heading="## H"),
    )

    result = service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=stale_sha,
        edit=EditSpec(content="- c", mode="append", target_heading="## H"),
    )

    assert result["stale_sha"] is True
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert "- c" not in note.content


def test_restore_version_stale_expected_sha_rejected(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Hist", "v1", [])["note_id"]
    sha1 = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace)).sha
    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha1,
        edit=EditSpec(content="v2"),
    )
    result = service.restore_version(
        note_id, sha1, owner_id="u1", ws_path=str(workspace), expected_sha="0" * 12
    )
    assert result["stale_sha"] is True
    assert service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace)).content == "v2"
