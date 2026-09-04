import pytest
from dulwich.repo import Repo

from kajet_turbo.markdown import EditSpec
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import NoteLinkRepository, NoteRepository
from tests.services.conftest import seed_user
from tests.services.helpers import build_reconcile_wiring, edit_item


def test_target_creation_marks_only_dangling_source_and_reconciles(database, git_workspace_factory):
    seed_user(database, "u1")
    ws = git_workspace_factory("u1/ws")
    service, jobs, dirty, dangling, handler = build_reconcile_wiring(database, ws.parent.parent)

    source_id = service.save("u1", "ws", str(ws), "Source", "[[Target]]", [])["note_id"]
    assert dirty.list_dirty("u1", "ws") == {}
    target_id = service.save("u1", "ws", str(ws), "Target", "body", [])["note_id"]

    assert set(dirty.list_dirty("u1", "ws")) == {source_id}
    reconcile_jobs = [j for j in jobs.list_jobs("u1") if j.kind == "reconcile_links"]
    assert len(reconcile_jobs) == 1
    assert reconcile_jobs[0].dedup_key == "reconcile:u1:ws"
    head_before = Repo(str(ws)).head()

    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    assert NoteLinkRepository(database.engine).backlinks(target_id) == [source_id]
    assert dangling.exists("u1", "ws") is False
    assert dirty.list_dirty("u1", "ws") == {}
    assert Repo(str(ws)).head() == head_before  # handler never writes Git


def test_generation_ack_does_not_delete_newer_marker(database):
    seed_user(database, "u1")
    jobs = JobRepository(database.engine)
    dirty = LinkReconcileRepository(database.engine, jobs)
    dirty.mark_and_enqueue("u1", "ws", {"source"})
    observed = dirty.list_dirty("u1", "ws")
    dirty.mark_and_enqueue("u1", "ws", {"source"})

    dirty.acknowledge("u1", "ws", observed)

    assert dirty.list_dirty("u1", "ws") == {"source": 2}
    assert len([j for j in jobs.list_jobs("u1") if j.kind == "reconcile_links"]) == 1


@pytest.mark.parametrize("mutation", ["update", "edit_many", "delete"])
def test_concurrent_source_mutation_cannot_leave_stale_graph(
    database, git_workspace_factory, monkeypatch, mutation
):
    seed_user(database, "u1")
    ws = git_workspace_factory("u1/ws")
    service, _jobs, dirty, _dangling, handler = build_reconcile_wiring(database, ws.parent.parent)
    first_id = service.save("u1", "ws", str(ws), "First", "body", [])["note_id"]
    second_id = service.save("u1", "ws", str(ws), "Second", "body", [])["note_id"]
    source_id = service.save("u1", "ws", str(ws), "Source", "[[First]]", [])["note_id"]
    dirty.mark_and_enqueue("u1", "ws", {source_id})

    original = service._link_service.persist_many
    raced = False

    def persist_after_mutation(*args, **kwargs):
        nonlocal raced
        resolutions = args[2]
        if not raced and source_id in resolutions:
            raced = True
            sha = service.get_history(source_id, "u1", str(ws))[0]["sha"]
            if mutation == "update":
                service.update(source_id, "u1", str(ws), sha, edit=EditSpec(content="[[Second]]"))
            elif mutation == "edit_many":
                result = service.edit_many(
                    "u1",
                    "ws",
                    str(ws),
                    [
                        edit_item(
                            source_id,
                            sha,
                            mode="replace_text",
                            old_str="[[First]]",
                            new_str="[[Second]]",
                        )
                    ],
                )
                assert result["applied"] is True
            else:
                service.delete(source_id, "u1", str(ws), expected_sha=sha)
        original(*args, **kwargs)

    monkeypatch.setattr(service._link_service, "persist_many", persist_after_mutation)
    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    links = NoteLinkRepository(database.engine)
    assert links.backlinks(first_id) == []
    assert links.backlinks(second_id) == ([] if mutation == "delete" else [source_id])
    assert dirty.list_dirty("u1", "ws") == {}


def test_dirty_markers_roll_back_when_enqueue_fails(database, monkeypatch):
    seed_user(database, "u1")
    jobs = JobRepository(database.engine)
    dirty = LinkReconcileRepository(database.engine, jobs)

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(jobs, "enqueue_in_session", fail_enqueue)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        dirty.mark_and_enqueue("u1", "ws", {"source"})

    assert dirty.list_dirty("u1", "ws") == {}


def test_missing_source_file_is_logged_cleaned_and_acknowledged(database, git_workspace_factory):
    seed_user(database, "u1")
    ws = git_workspace_factory("u1/ws")
    service, _jobs, dirty, dangling, handler = build_reconcile_wiring(database, ws.parent.parent)
    notes = NoteRepository(database.engine)
    links = NoteLinkRepository(database.engine)
    notes.insert("source", "ws", "u1", "Missing", [], "now", "now")
    links.replace_links("source", "ws", "u1", {"target"})
    dangling.replace_for_source("source", "ws", "u1", [("", "Other")])
    dirty.mark_and_enqueue("u1", "ws", {"source"})

    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    assert links.outlinks("source") == []
    assert dangling.exists("u1", "ws") is False
    assert dirty.list_dirty("u1", "ws") == {}
    assert service is not None


def test_legacy_heal_payload_uses_new_handler_without_dirty_marker(
    database, git_workspace_factory, note_file_factory
):
    seed_user(database, "u1")
    ws = git_workspace_factory("u1/ws")
    _service, _jobs, dirty, dangling, handler = build_reconcile_wiring(database, ws.parent.parent)
    notes = NoteRepository(database.engine)
    links = NoteLinkRepository(database.engine)
    notes.insert("source", "ws", "u1", "Source", [], "now", "now")
    notes.insert("target", "ws", "u1", "Target", [], "now", "now")
    note_file_factory(ws, "Source", note_id="source", content="[[Target]]")
    dangling.replace_for_source("source", "ws", "u1", [("", "Target")])

    handler({"user_id": "u1", "workspace": "ws"})

    assert links.backlinks("target") == ["source"]
    assert dangling.exists("u1", "ws") is False
    assert dirty.list_dirty("u1", "ws") == {}


def test_targeted_job_does_not_scan_unmarked_dangling_sources(
    database, git_workspace_factory, note_file_factory
):
    seed_user(database, "u1")
    ws = git_workspace_factory("u1/ws")
    _service, _jobs, dirty, dangling, handler = build_reconcile_wiring(database, ws.parent.parent)
    notes = NoteRepository(database.engine)
    links = NoteLinkRepository(database.engine)
    for source_id in ("source-1", "source-2"):
        notes.insert(source_id, "ws", "u1", source_id, [], "now", "now")
        note_file_factory(ws, source_id, note_id=source_id, content="[[Target]]")
        dangling.replace_for_source(source_id, "ws", "u1", [("", "Target")])
    notes.insert("target", "ws", "u1", "Target", [], "now", "now")
    dirty.mark_and_enqueue("u1", "ws", {"source-1"})

    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    assert links.backlinks("target") == ["source-1"]
    assert {row["source_note_id"] for row in dangling.list_for_workspace("u1", "ws")} == {
        "source-2"
    }


def test_all_identity_paths_share_one_snapshot_and_mark_targeted_sources(
    database, git_workspace_factory, monkeypatch
):
    seed_user(database, "u1")
    ws = git_workspace_factory("u1/ws")
    service, _jobs, dirty, _dangling, handler = build_reconcile_wiring(database, ws.parent.parent)
    target_id = service.save("u1", "ws", str(ws), "Target", "body", [], folder="Old")["note_id"]
    source_id = service.save("u1", "ws", str(ws), "Source", "[[Old/Target]]", [])["note_id"]

    calls = 0
    original = service._crud_repo.list_paths

    def counted_list_paths(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service._crud_repo, "list_paths", counted_list_paths)

    def one_snapshot(fn):
        before = calls
        result = fn()
        assert calls - before == 1
        return result

    sha = service.get_history(target_id, "u1", str(ws))[0]["sha"]
    one_snapshot(
        lambda: service.update(
            target_id,
            "u1",
            str(ws),
            sha,
            title="Renamed",
        )
    )
    assert set(dirty.list_dirty("u1", "ws")) == {source_id, target_id}
    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    one_snapshot(lambda: service.move(target_id, "u1", str(ws), "Mid"))
    assert set(dirty.list_dirty("u1", "ws")) == {source_id, target_id}
    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    one_snapshot(
        lambda: service.move_folder("Mid", "New", owner_id="u1", ws_path=str(ws), workspace="ws")
    )
    assert set(dirty.list_dirty("u1", "ws")) == {source_id, target_id}
    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    one_snapshot(lambda: service.delete(target_id, "u1", str(ws)))
    assert set(dirty.list_dirty("u1", "ws")) == {source_id}
    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    saved = one_snapshot(
        lambda: service.save_many(
            "u1",
            "ws",
            str(ws),
            [{"title": "Renamed", "folder": "New", "content": "body"}],
        )
    )
    replacement_id = saved[0]["note_id"]
    assert set(dirty.list_dirty("u1", "ws")) == {source_id}
    handler({"user_id": "u1", "workspace": "ws", "mode": "targeted"})

    replacement_sha = service.get_history(replacement_id, "u1", str(ws))[0]["sha"]
    one_snapshot(
        lambda: service.delete_many(
            "u1",
            "ws",
            str(ws),
            [{"note_id": replacement_id, "expected_sha": replacement_sha}],
        )
    )
    assert set(dirty.list_dirty("u1", "ws")) == {source_id}
