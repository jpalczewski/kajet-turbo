"""NoteLinkService.graph(): whole-workspace node/edge/dangling-link assembly."""

from tests.services.helpers import make_service_with_dangling


def test_graph_includes_isolated_notes_as_nodes(service, workspace):
    service.save("u1", "ws", str(workspace), "Lonely", "no links here", [])
    graph = service.graph("ws", "u1")
    assert [n["title"] for n in graph["nodes"]] == ["Lonely"]
    assert graph["edges"] == []


def test_graph_edge_shape(service, workspace):
    target_id = service.save("u1", "ws", str(workspace), "Target", "t", [])["note_id"]
    source_id = service.save("u1", "ws", str(workspace), "Source", "[[Target]]", [])["note_id"]
    graph = service.graph("ws", "u1")
    assert graph["edges"] == [{"source": source_id, "target": target_id}]
    node_ids = {n["note_id"] for n in graph["nodes"]}
    assert node_ids == {source_id, target_id}


def test_graph_dangling_links_none_when_not_tracked(service, workspace):
    """Default `service` fixture has no dangling_repo — validation is effectively on."""
    service.save("u1", "ws", str(workspace), "Note", "body", [])
    graph = service.graph("ws", "u1")
    assert graph["dangling_links"] is None


def test_graph_dangling_links_empty_list_when_tracked_and_clean(database, workspace):
    svc, _dangling = make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    svc.save("u1", "ws", str(workspace), "Note", "body", [])
    graph = svc.graph("ws", "u1")
    assert graph["dangling_links"] == []


def test_graph_includes_dangling_links_when_validation_off(database, workspace):
    svc, _dangling = make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    source_id = svc.save("u1", "ws", str(workspace), "Source", "[[Ghost]]", [])["note_id"]
    graph = svc.graph("ws", "u1")
    assert graph["dangling_links"] == [
        {"source_note_id": source_id, "target_folder": "", "target_title": "Ghost"}
    ]


def test_graph_cross_workspace_edge_target_included_with_real_workspace(service, workspace):
    target_id = service.save("u1", "ws2", str(workspace), "Target", "", [])["note_id"]
    source_id = service.save(
        "u1", "ws1", str(workspace), "Source", f"link to [[note:{target_id}]]", []
    )["note_id"]
    graph = service.graph("ws1", "u1")
    assert graph["edges"] == [{"source": source_id, "target": target_id}]
    target_node = next(n for n in graph["nodes"] if n["note_id"] == target_id)
    assert target_node["workspace"] == "ws2"


def test_graph_drops_edge_with_unresolved_endpoint(service, workspace):
    """A note_links row pointing at a note that no longer exists (e.g. a cross-workspace
    target wiped by clear_workspace_data, which only clears a deleted workspace's own
    outgoing edges — see the comment in NoteLinkService._build_graph) is dropped from
    edges, not surfaced as a broken node reference."""
    source_id = service.save("u1", "ws", str(workspace), "Source", "no links", [])["note_id"]
    service._link_service._link_repo.add_link(source_id, "does-not-exist", "ws", "u1")
    graph = service.graph("ws", "u1")
    assert graph["edges"] == []
    assert [n["note_id"] for n in graph["nodes"]] == [source_id]


def test_neighborhood_walks_both_directions_and_returns_induced_edges(service, workspace):
    c_id = service.save("u1", "ws", str(workspace), "C", "", [])["note_id"]
    b_id = service.save("u1", "ws", str(workspace), "B", "[[C]]", [])["note_id"]
    a_id = service.save("u1", "ws", str(workspace), "A", "[[B]]", [])["note_id"]
    d_id = service.save("u1", "ws", str(workspace), "D", "[[B]]", [])["note_id"]

    one_hop = service.neighborhood(a_id, "ws", "u1", depth=1)
    assert {node["note_id"] for node in one_hop["nodes"]} == {a_id, b_id}
    assert one_hop["edges"] == [{"source": a_id, "target": b_id}]

    two_hops = service.neighborhood(a_id, "ws", "u1", depth=2)
    assert {node["note_id"] for node in two_hops["nodes"]} == {a_id, b_id, c_id, d_id}
    assert {(edge["source"], edge["target"]) for edge in two_hops["edges"]} == {
        (a_id, b_id),
        (b_id, c_id),
        (d_id, b_id),
    }


def test_neighborhood_cross_workspace_is_opt_in(service, workspace):
    y_id = service.save("u1", "ws2", str(workspace), "Y", "", [])["note_id"]
    x_id = service.save("u1", "ws2", str(workspace), "X", "[[Y]]", [])["note_id"]
    a_id = service.save("u1", "ws1", str(workspace), "A", f"[[note:{x_id}]]", [])["note_id"]

    local = service.neighborhood(a_id, "ws1", "u1", depth=2)
    assert [node["note_id"] for node in local["nodes"]] == [a_id]
    assert local["edges"] == []

    expanded = service.neighborhood(a_id, "ws1", "u1", depth=2, include_cross_workspace=True)
    assert {node["note_id"] for node in expanded["nodes"]} == {a_id, x_id, y_id}
    assert {(edge["source"], edge["target"]) for edge in expanded["edges"]} == {
        (a_id, x_id),
        (x_id, y_id),
    }


def test_neighborhood_limits_dangling_links_to_neighborhood_sources(database, workspace):
    svc, _dangling = make_service_with_dangling(
        database, link_validation_enabled=lambda ws, owner: False
    )
    center_id = svc.save("u1", "ws", str(workspace), "Center", "", [])["note_id"]
    source_id = svc.save("u1", "ws", str(workspace), "Source", "[[Center]] and [[Ghost]]", [])[
        "note_id"
    ]
    svc.save("u1", "ws", str(workspace), "Elsewhere", "[[Other ghost]]", [])

    graph = svc.neighborhood(center_id, "ws", "u1", depth=1)
    assert graph["dangling_links"] == [
        {"source_note_id": source_id, "target_folder": "", "target_title": "Ghost"}
    ]


def test_neighborhood_requires_center_in_requested_workspace(service, workspace):
    note_id = service.save("u1", "other", str(workspace), "Elsewhere", "", [])["note_id"]
    assert service.neighborhood(note_id, "ws", "u1") is None
