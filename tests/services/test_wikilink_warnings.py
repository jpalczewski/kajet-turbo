def _seed_candidates(service, workspace):
    service.save("u1", "ws", str(workspace), "README", "near", [], folder="Project")
    service.save("u1", "ws", str(workspace), "README", "far", [], folder="Archive")


def _warning():
    return {
        "kind": "ambiguous_wikilink",
        "target": "README",
        "resolved_to": "Project/README",
        "alternatives": ["Archive/README"],
    }


def _case_corrected_warning():
    return {
        "kind": "case_corrected_wikilink",
        "target": "plan projektu",
        "resolved_to": "Plan projektu",
    }


def test_save_reports_ambiguity_without_rejecting(service, workspace):
    _seed_candidates(service, workspace)

    result = service.save("u1", "ws", str(workspace), "Source", "[[README]]", [], folder="Project")

    assert result["note_id"]
    assert result["warnings"] == [_warning()]


def test_update_and_batch_writes_report_ambiguity(service, workspace):
    _seed_candidates(service, workspace)
    source = service.save("u1", "ws", str(workspace), "Source", "body", [], folder="Project")
    sha = service.get_history(source["note_id"], "u1", str(workspace))[0]["sha"]

    updated = service.update(
        source["note_id"],
        "u1",
        str(workspace),
        sha,
        content="[[README]]",
    )
    created = service.save_many(
        "u1",
        "ws",
        str(workspace),
        [{"title": "Batch", "folder": "Project", "content": "[[README]]"}],
    )

    assert updated["warnings"] == [_warning()]
    assert created[0]["warnings"] == [_warning()]

    latest_sha = service.get_history(source["note_id"], "u1", str(workspace))[0]["sha"]
    edited = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            {
                "note_id": source["note_id"],
                "expected_sha": latest_sha,
                "mode": "overwrite",
                "content": "again [[README]]",
            }
        ],
    )
    assert edited["results"][0]["warnings"] == [_warning()]


def test_save_reports_case_corrected_without_rejecting(service, workspace):
    service.save("u1", "ws", str(workspace), "Plan projektu", "cel", [])

    result = service.save("u1", "ws", str(workspace), "Source", "[[plan projektu]]", [])

    assert result["note_id"]
    assert result["warnings"] == [_case_corrected_warning()]


def test_update_and_batch_writes_report_case_corrected(service, workspace):
    service.save("u1", "ws", str(workspace), "Plan projektu", "cel", [])
    source = service.save("u1", "ws", str(workspace), "Source", "body", [])
    sha = service.get_history(source["note_id"], "u1", str(workspace))[0]["sha"]

    updated = service.update(
        source["note_id"],
        "u1",
        str(workspace),
        sha,
        content="[[plan projektu]]",
    )
    created = service.save_many(
        "u1",
        "ws",
        str(workspace),
        [{"title": "Batch", "content": "[[plan projektu]]"}],
    )

    assert updated["warnings"] == [_case_corrected_warning()]
    assert created[0]["warnings"] == [_case_corrected_warning()]

    latest_sha = service.get_history(source["note_id"], "u1", str(workspace))[0]["sha"]
    edited = service.edit_many(
        "u1",
        "ws",
        str(workspace),
        [
            {
                "note_id": source["note_id"],
                "expected_sha": latest_sha,
                "mode": "overwrite",
                "content": "again [[plan projektu]]",
            }
        ],
    )
    assert edited["results"][0]["warnings"] == [_case_corrected_warning()]
