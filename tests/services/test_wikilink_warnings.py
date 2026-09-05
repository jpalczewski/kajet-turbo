import pytest

from kajet_turbo.markdown import EditSpec
from tests.services.conftest import note_target, seed_user, workspace_target
from tests.services.helpers import edit_item


@pytest.fixture(autouse=True)
def _seed_default_owner(database):
    # update()'s rewrite_backlinks leg now enqueues reindex_note jobs (user_id FK).
    seed_user(database, "u1")


def _seed_ambiguous(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "README", "near", [], folder="Project")
    service.save(workspace_target("u1", "ws", workspace), "README", "far", [], folder="Archive")


def _seed_case_corrected(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "Plan projektu", "cel", [])


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

    result = service.save(
        workspace_target("u1", "ws", workspace), "Source", content, [], folder=folder
    )

    assert result["note_id"]
    assert result["warnings"] == [expected]


@pytest.mark.parametrize("seed, folder, content, expected", WARNING_CASES)
def test_update_and_batch_writes_report_warning(
    service, workspace, seed, folder, content, expected
):
    seed(service, workspace)
    source = service.save(
        workspace_target("u1", "ws", workspace), "Source", "body", [], folder=folder
    )
    sha = service.get_history(note_target("u1", "ws", workspace, source["note_id"]))[0]["sha"]

    updated = service.update(
        note_target("u1", "ws", workspace, source["note_id"]),
        sha,
        edit=EditSpec(content=content),
    )
    created = service.save_many(
        workspace_target("u1", "ws", workspace),
        [{"title": "Batch", "folder": folder, "content": content}],
    )

    assert updated["warnings"] == [expected]
    assert created[0]["warnings"] == [expected]

    latest_sha = service.get_history(note_target("u1", "ws", workspace, source["note_id"]))[0][
        "sha"
    ]
    edited = service.edit_many(
        workspace_target("u1", "ws", workspace),
        [edit_item(source["note_id"], latest_sha, mode="overwrite", content=f"again {content}")],
    )
    assert edited["results"][0]["warnings"] == [expected]
