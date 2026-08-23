import pytest


def _seed_ambiguous(service, workspace):
    service.save("u1", "ws", str(workspace), "README", "near", [], folder="Project")
    service.save("u1", "ws", str(workspace), "README", "far", [], folder="Archive")


def _seed_case_corrected(service, workspace):
    service.save("u1", "ws", str(workspace), "Plan projektu", "cel", [])


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
        "alternatives": [],
    }


WARNING_CASES = [
    pytest.param(_seed_ambiguous, "Project", "[[README]]", _warning(), id="ambiguous"),
    pytest.param(
        _seed_case_corrected,
        "",
        "[[plan projektu]]",
        _case_corrected_warning(),
        id="case_corrected",
    ),
]


@pytest.mark.parametrize("seed, folder, content, expected", WARNING_CASES)
def test_save_reports_warning_without_rejecting(
    service, workspace, seed, folder, content, expected
):
    seed(service, workspace)

    result = service.save("u1", "ws", str(workspace), "Source", content, [], folder=folder)

    assert result["note_id"]
    assert result["warnings"] == [expected]


@pytest.mark.parametrize("seed, folder, content, expected", WARNING_CASES)
def test_update_and_batch_writes_report_warning(
    service, workspace, seed, folder, content, expected
):
    seed(service, workspace)
    source = service.save("u1", "ws", str(workspace), "Source", "body", [], folder=folder)
    sha = service.get_history(source["note_id"], "u1", str(workspace))[0]["sha"]

    updated = service.update(
        source["note_id"],
        "u1",
        str(workspace),
        sha,
        content=content,
    )
    created = service.save_many(
        "u1",
        "ws",
        str(workspace),
        [{"title": "Batch", "folder": folder, "content": content}],
    )

    assert updated["warnings"] == [expected]
    assert created[0]["warnings"] == [expected]

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
                "content": f"again {content}",
            }
        ],
    )
    assert edited["results"][0]["warnings"] == [expected]
