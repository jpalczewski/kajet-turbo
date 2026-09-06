"""NoteLinkService.graph(): whole-workspace node/edge/dangling-link assembly."""

from tests.services.conftest import note_target, workspace_target
from tests.services.helpers import make_service_with_dangling


def test_graph_includes_isolated_notes_as_nodes(service, link_service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "Lonely", "no links here", [])
    graph = link_service.graph(workspace_target("u1", "ws", workspace))
    assert [n["title"] for n in graph["nodes"]] == ["Lonely"]
    assert graph["nodes"][0]["kind"] == "note"
    assert graph["nodes"][0]["id"] == graph["nodes"][0]["note_id"]
    assert graph["edges"] == []


def test_graph_edge_shape(service, link_service, workspace):
    target_id = service.save(workspace_target("u1", "ws", workspace), "Target", "t", [])["note_id"]
    source_id = service.save(workspace_target("u1", "ws", workspace), "Source", "[[Target]]", [])[
        "note_id"
    ]
    graph = link_service.graph(workspace_target("u1", "ws", workspace))
    assert graph["edges"] == [{"source": source_id, "target": target_id}]
    node_ids = {n["note_id"] for n in graph["nodes"]}
    assert node_ids == {source_id, target_id}


def test_graph_adds_tag_hubs_without_pairwise_note_edges(service, link_service, workspace):
    first_id = service.save(
        workspace_target("u1", "ws", workspace), "First", "", ["work/projects"]
    )["note_id"]
    second_id = service.save(
        workspace_target("u1", "ws", workspace), "Second", "", ["work/projects"]
    )["note_id"]

    target = workspace_target("u1", "ws", workspace)
    without_tags = link_service.graph(target)
    graph = link_service.graph(target, include_tags=True)
    tags = {node["path"]: node for node in graph["nodes"] if node["kind"] == "tag"}

    assert {node["kind"] for node in without_tags["nodes"]} == {"note"}
    assert without_tags["edges"] == []
    assert set(tags) == {"work", "work/projects"}
    project_id = tags["work/projects"]["id"]
    work_id = tags["work"]["id"]
    assert {(edge["source"], edge["target"]) for edge in graph["edges"]} == {
        (first_id, project_id),
        (second_id, project_id),
        (project_id, work_id),
    }


def test_neighborhood_tags_are_limited_to_returned_notes(service, link_service, workspace):
    target_id = service.save(
        workspace_target("u1", "ws", workspace), "Target", "", ["work/projects"]
    )["note_id"]
    source_id = service.save(
        workspace_target("u1", "ws", workspace), "Source", "[[Target]]", ["people"]
    )["note_id"]
    service.save(workspace_target("u1", "ws", workspace), "Elsewhere", "", ["secret"])

    graph = link_service.neighborhood(
        note_target("u1", "ws", workspace, source_id), depth=1, include_tags=True
    )
    tags = {node["path"]: node for node in graph["nodes"] if node["kind"] == "tag"}

    assert set(tags) == {"people", "work", "work/projects"}
    people_id = tags["people"]["id"]
    project_id = tags["work/projects"]["id"]
    assert {edge["source"] for edge in graph["edges"] if edge["target"] == people_id} == {source_id}
    project_sources = {edge["source"] for edge in graph["edges"] if edge["target"] == project_id}
    assert project_sources == {target_id}


def test_graph_dangling_links_none_when_not_tracked(service, link_service, workspace):
    """Default `service` fixture has no dangling_repo — validation is effectively on."""
    service.save(workspace_target("u1", "ws", workspace), "Note", "body", [])
    graph = link_service.graph(workspace_target("u1", "ws", workspace))
    assert graph["dangling_links"] is None


def test_graph_dangling_links_empty_list_when_tracked_and_clean(database, workspace):
    svc, _dangling = make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    svc.save(workspace_target("u1", "ws", workspace), "Note", "body", [])
    graph = svc._link_service.graph(workspace_target("u1", "ws", workspace))
    assert graph["dangling_links"] == []


def test_graph_includes_dangling_links_when_validation_off(database, workspace):
    svc, _dangling = make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    source_id = svc.save(workspace_target("u1", "ws", workspace), "Source", "[[Ghost]]", [])[
        "note_id"
    ]
    graph = svc._link_service.graph(workspace_target("u1", "ws", workspace))
    assert graph["dangling_links"] == [
        {"source_note_id": source_id, "target_folder": "", "target_title": "Ghost"}
    ]


def test_graph_cross_workspace_edge_target_included_with_real_workspace(
    service, link_service, workspace
):
    target_id = service.save(workspace_target("u1", "ws2", workspace), "Target", "", [])["note_id"]
    source_id = service.save(
        workspace_target("u1", "ws1", workspace), "Source", f"link to [[note:{target_id}]]", []
    )["note_id"]
    graph = link_service.graph(workspace_target("u1", "ws1", workspace))
    assert graph["edges"] == [{"source": source_id, "target": target_id}]
    target_node = next(n for n in graph["nodes"] if n["note_id"] == target_id)
    assert target_node["workspace"] == "ws2"


def test_graph_tags_keep_cross_workspace_hubs_separate(service, link_service, workspace):
    target_id = service.save(workspace_target("u1", "ws2", workspace), "Target", "", ["work"])[
        "note_id"
    ]
    source_id = service.save(
        workspace_target("u1", "ws1", workspace), "Source", f"[[note:{target_id}]]", ["work"]
    )["note_id"]

    graph = link_service.graph(workspace_target("u1", "ws1", workspace), include_tags=True)
    work_tags = [
        node for node in graph["nodes"] if node["kind"] == "tag" and node["path"] == "work"
    ]

    assert {node["workspace"] for node in work_tags} == {"ws1", "ws2"}
    assert len({node["id"] for node in work_tags}) == 2
    assert {(edge["source"], edge["target"]) for edge in graph["edges"]} >= {
        (source_id, target_id),
        (source_id, next(node["id"] for node in work_tags if node["workspace"] == "ws1")),
        (target_id, next(node["id"] for node in work_tags if node["workspace"] == "ws2")),
    }


def test_graph_drops_edge_with_unresolved_endpoint(service, link_service, workspace):
    """A note_links row pointing at a note that no longer exists (e.g. a cross-workspace
    target wiped by clear_workspace_data, which only clears a deleted workspace's own
    outgoing edges — see the comment in NoteLinkService._build_graph) is dropped from
    edges, not surfaced as a broken node reference."""
    source_id = service.save(workspace_target("u1", "ws", workspace), "Source", "no links", [])[
        "note_id"
    ]
    link_service._link_repo.add_link(source_id, "does-not-exist", "ws", "u1")
    graph = link_service.graph(workspace_target("u1", "ws", workspace))
    assert graph["edges"] == []
    assert [n["note_id"] for n in graph["nodes"]] == [source_id]


def test_neighborhood_walks_both_directions_and_returns_induced_edges(
    service, link_service, workspace
):
    c_id = service.save(workspace_target("u1", "ws", workspace), "C", "", [])["note_id"]
    b_id = service.save(workspace_target("u1", "ws", workspace), "B", "[[C]]", [])["note_id"]
    a_id = service.save(workspace_target("u1", "ws", workspace), "A", "[[B]]", [])["note_id"]
    d_id = service.save(workspace_target("u1", "ws", workspace), "D", "[[B]]", [])["note_id"]

    target = note_target("u1", "ws", workspace, a_id)
    one_hop = link_service.neighborhood(target, depth=1)
    assert {node["note_id"] for node in one_hop["nodes"]} == {a_id, b_id}
    assert one_hop["edges"] == [{"source": a_id, "target": b_id}]

    two_hops = link_service.neighborhood(target, depth=2)
    assert {node["note_id"] for node in two_hops["nodes"]} == {a_id, b_id, c_id, d_id}
    assert {(edge["source"], edge["target"]) for edge in two_hops["edges"]} == {
        (a_id, b_id),
        (b_id, c_id),
        (d_id, b_id),
    }


def test_neighborhood_cross_workspace_is_opt_in(service, link_service, workspace):
    y_id = service.save(workspace_target("u1", "ws2", workspace), "Y", "", [])["note_id"]
    x_id = service.save(workspace_target("u1", "ws2", workspace), "X", "[[Y]]", [])["note_id"]
    a_id = service.save(workspace_target("u1", "ws1", workspace), "A", f"[[note:{x_id}]]", [])[
        "note_id"
    ]

    target = note_target("u1", "ws1", workspace, a_id)
    local = link_service.neighborhood(target, depth=2)
    assert [node["note_id"] for node in local["nodes"]] == [a_id]
    assert local["edges"] == []

    expanded = link_service.neighborhood(target, depth=2, include_cross_workspace=True)
    assert {node["note_id"] for node in expanded["nodes"]} == {a_id, x_id, y_id}
    assert {(edge["source"], edge["target"]) for edge in expanded["edges"]} == {
        (a_id, x_id),
        (x_id, y_id),
    }


def test_neighborhood_includes_cross_workspace_note_tags(service, link_service, workspace):
    target_id = service.save(
        workspace_target("u1", "ws2", workspace), "Target", "", ["work/projects"]
    )["note_id"]
    source_id = service.save(
        workspace_target("u1", "ws1", workspace), "Source", f"[[note:{target_id}]]", ["people"]
    )["note_id"]

    graph = link_service.neighborhood(
        note_target("u1", "ws1", workspace, source_id),
        depth=1,
        include_cross_workspace=True,
        include_tags=True,
    )
    tags = {(node["workspace"], node["path"]) for node in graph["nodes"] if node["kind"] == "tag"}

    assert tags == {("ws1", "people"), ("ws2", "work"), ("ws2", "work/projects")}


def test_neighborhood_limits_dangling_links_to_neighborhood_sources(database, workspace):
    svc, _dangling = make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    center_id = svc.save(workspace_target("u1", "ws", workspace), "Center", "", [])["note_id"]
    source_id = svc.save(
        workspace_target("u1", "ws", workspace), "Source", "[[Center]] and [[Ghost]]", []
    )["note_id"]
    svc.save(workspace_target("u1", "ws", workspace), "Elsewhere", "[[Other ghost]]", [])

    graph = svc._link_service.neighborhood(note_target("u1", "ws", workspace, center_id), depth=1)
    assert graph["dangling_links"] == [
        {"source_note_id": source_id, "target_folder": "", "target_title": "Ghost"}
    ]


def test_neighborhood_requires_center_in_requested_workspace(link_service, service, workspace):
    note_id = service.save(workspace_target("u1", "other", workspace), "Elsewhere", "", [])[
        "note_id"
    ]
    assert link_service.neighborhood(note_target("u1", "ws", workspace, note_id)) is None
