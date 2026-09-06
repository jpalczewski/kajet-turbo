"""get_note_links tool coverage."""

import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kajet_turbo.repositories.git import GitRepository
from tests.mcp_tools.helpers import call_json, seed_note


async def test_get_note_links(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Target", "content": "content"},
                )
            )
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {
                        "workspace": "test-ws",
                        "title": "Source",
                        "content": "see [[Target]]",
                    },
                )
            )
            .content[0]
            .text
        )["note_id"]

        # outlinks of Source → Target
        result = json.loads(
            (await client.call_tool("get_note_links", {"note_id": source_id})).content[0].text
        )
        assert result["outlinks"] == [
            {
                "note_id": target_id,
                "title": "Target",
                "folder": "",
                "workspace": "test-ws",
                "tags": None,
                "updated_at": None,
            }
        ]
        assert result["backlinks"] == []

        # backlinks of Target → Source
        result = json.loads(
            (await client.call_tool("get_note_links", {"note_id": target_id})).content[0].text
        )
        assert result["backlinks"] == [
            {
                "note_id": source_id,
                "title": "Source",
                "folder": "",
                "workspace": "test-ws",
                "tags": None,
                "updated_at": None,
            }
        ]
        assert result["outlinks"] == []


async def test_get_note_links_not_found(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_note_links", {"note_id": "nonexistent"})


async def test_get_note_links_include_meta(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {
                        "workspace": "test-ws",
                        "title": "Tagged",
                        "content": "content",
                        "tags": ["work"],
                    },
                )
            )
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Linker", "content": "[[Tagged]]"},
                )
            )
            .content[0]
            .text
        )["note_id"]

        result = json.loads(
            (await client.call_tool("get_note_links", {"note_id": source_id, "include_meta": True}))
            .content[0]
            .text
        )
        entry = result["outlinks"][0]
        assert entry["note_id"] == target_id
        assert "tags" in entry
        assert entry["tags"] == ["work"]
        assert "updated_at" in entry


async def test_get_note_links_exclude_cross_workspace(workspaces_dir, mcp_server):
    """include_cross_workspace=False parameter on MCP tool hides cross-workspace backlinks."""
    for ws_name in ("ws-a", "ws-b"):
        ws_path = workspaces_dir / ws_name
        ws_path.mkdir()
        GitRepository.init(str(ws_path))
        mcp_server.workspace_repo.grant_access("u1", ws_name)

    mcp, _ = mcp_server
    async with Client(mcp) as client:
        # Create target note in ws-b.
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "ws-b", "title": "Target", "content": "content"},
                )
            )
            .content[0]
            .text
        )["note_id"]

        # Create source note in ws-a with a cross-workspace link to target.
        await client.call_tool(
            "save_note",
            {"workspace": "ws-a", "title": "Source", "content": f"[[note:{target_id}]]"},
        )

        # Verify that include_cross_workspace=False hides the backlink.
        result = json.loads(
            (
                await client.call_tool(
                    "get_note_links",
                    {"note_id": target_id, "include_cross_workspace": False},
                )
            )
            .content[0]
            .text
        )

    assert result["backlinks"] == []


async def test_get_workspace_graph(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Target", "content": "content"},
                )
            )
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {
                        "workspace": "test-ws",
                        "title": "Source",
                        "content": "see [[Target]]",
                    },
                )
            )
            .content[0]
            .text
        )["note_id"]
        json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Lonely", "content": "no links"},
                )
            )
            .content[0]
            .text
        )

        result = json.loads(
            (await client.call_tool("get_workspace_graph", {"workspace": "test-ws"}))
            .content[0]
            .text
        )

    nodes = {n["title"]: n for n in result["nodes"]}
    assert set(nodes) == {"Target", "Source", "Lonely"}
    assert nodes["Source"]["note_id"] == source_id
    assert nodes["Source"]["links"] == [target_id]
    assert nodes["Target"]["links"] == []
    # Ranked by degree, so the unlinked note sorts last.
    assert result["nodes"][-1]["title"] == "Lonely"
    # A note node is addressed by note_id; the duplicate graph `id` and the
    # same-workspace `workspace` are both dropped on the wire.
    assert "id" not in nodes["Source"]
    assert nodes["Source"]["workspace"] is None
    assert (result["total_nodes"], result["total_edges"], result["next_offset"]) == (3, 1, None)
    assert result["dangling_links"] is None


async def test_get_note_neighborhood(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        target_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Target", "content": "content"},
                )
            )
            .content[0]
            .text
        )["note_id"]
        source_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {"workspace": "test-ws", "title": "Source", "content": "[[Target]]"},
                )
            )
            .content[0]
            .text
        )["note_id"]
        result = json.loads(
            (await client.call_tool("get_note_neighborhood", {"note_id": source_id}))
            .content[0]
            .text
        )

    nodes = {n["note_id"]: n for n in result["nodes"]}
    assert nodes[source_id]["links"] == [target_id]
    assert nodes[target_id]["links"] == []
    # Never paged: the totals describe exactly what came back.
    assert (result["total_nodes"], result["total_edges"], result["next_offset"]) == (2, 1, None)


async def test_get_workspace_graph_includes_tag_hubs(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        source_id = json.loads(
            (
                await client.call_tool(
                    "save_note",
                    {
                        "workspace": "test-ws",
                        "title": "Source",
                        "content": "",
                        "tags": ["work/projects"],
                    },
                )
            )
            .content[0]
            .text
        )["note_id"]
        result = json.loads(
            (
                await client.call_tool(
                    "get_workspace_graph", {"workspace": "test-ws", "include_tags": True}
                )
            )
            .content[0]
            .text
        )

    tags = {node["path"]: node for node in result["nodes"] if node["kind"] == "tag"}
    assert {"work", "work/projects"} == set(tags)
    source = next(node for node in result["nodes"] if node.get("note_id") == source_id)
    assert source["links"] == [tags["work/projects"]["id"]]
    assert tags["work/projects"]["links"] == [tags["work"]["id"]]
    assert tags["work"]["links"] == []
    # Tag hubs ride along with their notes but are not the paged unit, and the links
    # into them are not graph edges — get_note_neighborhood counts them the same way.
    assert (result["total_nodes"], result["total_edges"]) == (1, 0)


async def test_get_workspace_graph_pages_by_degree(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:

        async def save(title: str, content: str) -> str:
            return (await seed_note(client, workspace="test-ws", title=title, content=content))[
                "note_id"
            ]

        spokes = {title: await save(title, "") for title in ("A", "B", "C")}
        hub_id = await save("Hub", "[[A]] [[B]] [[C]]")
        await save("Lonely", "")

        async def page(offset: int) -> dict:
            return await call_json(
                client,
                "get_workspace_graph",
                {"workspace": "test-ws", "limit": 2, "offset": offset},
            )

        first = await page(0)
        pages, offset = [first], first["next_offset"]
        while offset is not None:
            pages.append(await page(offset))
            offset = pages[-1]["next_offset"]

    assert [len(p["nodes"]) for p in pages] == [2, 2, 1]
    assert [p["next_offset"] for p in pages] == [2, 4, None]
    assert {p["total_nodes"] for p in pages} == {5}
    assert {p["total_edges"] for p in pages} == {3}
    # Highest degree first, so the hub leads page one and the isolated note trails.
    assert first["nodes"][0]["note_id"] == hub_id
    assert pages[-1]["nodes"][-1]["title"] == "Lonely"
    # Every link is emitted exactly once across the walk, by its source's page.
    walked = [
        (node["note_id"], link) for p in pages for node in p["nodes"] for link in node["links"]
    ]
    assert sorted(walked) == sorted((hub_id, spoke) for spoke in spokes.values())


async def test_get_workspace_graph_offset_past_the_end_is_empty(workspaces_dir, mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await seed_note(client, workspace="test-ws", title="Only")
        result = await call_json(
            client, "get_workspace_graph", {"workspace": "test-ws", "offset": 50}
        )

    assert result["nodes"] == []
    assert result["next_offset"] is None
    assert result["total_nodes"] == 1


@pytest.mark.parametrize(
    ("args", "rejected"),
    [({"limit": 0}, "limit"), ({"limit": 201}, "limit"), ({"offset": -1}, "offset")],
)
async def test_get_workspace_graph_rejects_out_of_range_paging(
    workspaces_dir, mcp_server, args, rejected
):
    """An out-of-range page is refused by name rather than silently clamped — a clamped
    page would report a next_offset for a window the caller never asked for."""
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        await seed_note(client, workspace="test-ws", title="Only")
        with pytest.raises(ToolError) as excinfo:
            await client.call_tool("get_workspace_graph", {"workspace": "test-ws", **args})
        # The refusal changed nothing: a valid page still reaches the whole workspace.
        intact = await call_json(client, "get_workspace_graph", {"workspace": "test-ws"})

    assert rejected in str(excinfo.value)
    assert [node["title"] for node in intact["nodes"]] == ["Only"]
