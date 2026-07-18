"""set_tags/update confirmation gating and stale expected_sha coverage."""


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


def test_update_requires_confirmation_on_content_overwrite(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "stara treść", [])["note_id"]
    before = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    result = service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, content="nowa treść"
    )

    assert result["requires_confirmation"] is True
    assert result["overwrites_content"] is True
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "stara treść"
    after = len(service.get_history(note_id, owner_id="u1", ws_path=str(workspace)))
    assert after == before


def test_update_confirm_applies_content_overwrite(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "stara treść", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        content="nowa treść",
        confirm=True,
    )

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
        content="pierwsza treść",
    )

    assert result.get("requires_confirmation") is None
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
        content="- b",
        mode="append",
        target_heading="## H",
    )

    assert result.get("requires_confirmation") is None


def test_update_requires_confirmation_on_tag_drop(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "treść", ["python", "work"])[
        "note_id"
    ]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    result = service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, tags=["python"]
    )

    assert result["requires_confirmation"] is True
    assert result["would_remove_tags"] == ["work"]


def test_update_rejects_stale_expected_sha(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Notka", "v1", [])["note_id"]
    stale_sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=stale_sha,
        content="v2",
        confirm=True,
    )

    result = service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=stale_sha,
        content="v3",
        confirm=True,
    )

    assert result["stale_sha"] is True
    assert "current_sha" not in result
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "v2"


def test_update_stale_sha_rejected_even_for_pure_append(service, workspace):
    # Zero data-loss risk (surgical append), but staleness gate is independent of intent.
    note_id = service.save("u1", "ws", str(workspace), "Notka", "## H\n\n- a", [])["note_id"]
    stale_sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=stale_sha,
        content="- b",
        mode="append",
        target_heading="## H",
    )

    result = service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=stale_sha,
        content="- c",
        mode="append",
        target_heading="## H",
    )

    assert result["stale_sha"] is True
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert "- c" not in note.content


def test_update_fresh_sha_destructive_overwrite_still_requires_confirm(service, workspace):
    # Fresh sha proves staleness isn't the issue — the intent gate still fires separately.
    note_id = service.save("u1", "ws", str(workspace), "Notka", "stara treść", [])["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    result = service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha, content="nowa treść"
    )

    assert result.get("stale_sha") is None
    assert result["requires_confirmation"] is True
