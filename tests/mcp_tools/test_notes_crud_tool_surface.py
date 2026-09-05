"""Regression test for the notes-CRUD MCP tool surface.

Captured once against the pre-split `mcp/notes/crud.py` (a single 656-line builder) as a
baseline, then re-verified unchanged after the tool split into
`mcp/notes/{write,read,search,temporal,maintenance}.py` (#219). If this test fails after a
future change, either the change accidentally altered a tool's public contract, or the
expected data below needs a deliberate update alongside it.
"""

import hashlib

from fastmcp import Client

# name -> (tags without "read"/"write", destructive_hint, idempotent_hint, read_only_hint,
#          sorted params excluding ws/ctx, description sha256)
EXPECTED: dict[str, tuple[frozenset[str], bool, bool, bool, tuple[str, ...], str]] = {
    "save_note": (
        frozenset({"notes", "crud"}),
        False,
        False,
        False,
        ("content", "folder", "occurred_at", "period", "tags", "title"),
        "2cbb8ac9db957c3df152e7b31f48dceef875528f010e42d95ad7b46fad1f6709",
    ),
    "save_notes": (
        frozenset({"notes", "crud"}),
        False,
        False,
        False,
        ("notes",),
        "fb3fa2142b8df834526c5dcf1480b8ba6b71ce6cf7b0878f792fb0267f34a06a",
    ),
    "get_note": (
        frozenset({"notes", "crud"}),
        False,
        True,
        True,
        ("folder", "note_id", "title"),
        "a6642b3261198b6a94095df20de4993f8d30e6584b18cfcbbd31593a2c756835",
    ),
    "get_notes": (
        frozenset({"notes", "crud"}),
        False,
        True,
        True,
        ("note_ids",),
        "bf88b12f7e2dd0109e541bdf114ab088099968e7b5475602bb20763e23ab11eb",
    ),
    "edit_note": (
        frozenset({"notes", "crud"}),
        True,
        False,
        False,
        (
            "clear_date_metadata",
            "content",
            "expected_sha",
            "folder",
            "mode",
            "new_str",
            "note_id",
            "occurred_at",
            "old_str",
            "period",
            "replace_all",
            "tags",
            "target_heading",
            "title",
        ),
        "b69d0ea6c9dd9920a52a8323020562b5b771d21b1c0c34781adb5d9f170f1233",
    ),
    "edit_notes": (
        frozenset({"notes", "crud"}),
        True,
        False,
        False,
        ("edits",),
        "0fd4e9aebb23814a6b36282195c46bc464933297307e9fe09a4891efa463a38d",
    ),
    "get_note_outline": (
        frozenset({"notes", "crud"}),
        False,
        True,
        True,
        ("note_id",),
        "6465727f2bde3a2b4c0e5503673fc4b89a426079a9f60ad4bf8f97f0cbec5cc3",
    ),
    "move_note": (
        frozenset({"notes", "crud"}),
        False,
        False,
        False,
        ("folder", "note_id"),
        "d0508f02e0c27fa9414a1e2f88a07d189e651042977963cc9c82895a51151a42",
    ),
    "delete_note": (
        frozenset({"notes", "crud"}),
        True,
        False,
        False,
        ("expected_sha", "note_id"),
        "4a10b82a95f7e196432e7f88d7eb7987805a9f21fcdd0e27ea81a01712486ba0",
    ),
    "delete_notes": (
        frozenset({"notes", "crud"}),
        True,
        False,
        False,
        ("deletes",),
        "e9e426651116e74643377952e4108827131b09556658e39e395cde7ba711e757",
    ),
    "list_notes": (
        frozenset({"notes", "crud"}),
        False,
        True,
        True,
        ("folder", "limit", "sort", "tags"),
        "965920ac70499feb621fd2e1a062ea3b8bd154f07d9c98f578144258b95cd1ea",
    ),
    "entries_in": (
        frozenset({"notes", "crud"}),
        False,
        True,
        True,
        ("collection", "folder", "period"),
        "7dfc2890364fd62fd65030e47b22aac091611a33dda5ebaa5e5f51aba55f6b1c",
    ),
    "export_folder": (
        frozenset({"notes", "crud"}),
        False,
        True,
        True,
        ("folder", "max_chars"),
        "3dc72f6edf56b46551e560fae24172e7de48f050cc757bb4f64fd7789e06bbad",
    ),
    "search_notes": (
        frozenset({"notes", "search"}),
        False,
        True,
        True,
        ("folder", "limit", "query", "tags", "workspace"),
        "5efe885fca043d69d9cdbf281496e464d2c488b3d92cac11b8fc97e37f8c4a3f",
    ),
    "grep_notes": (
        frozenset({"notes", "search"}),
        False,
        True,
        True,
        ("case_sensitive", "folder", "max_results", "pattern"),
        "e1881cd05017255196e974f8b7c1ca91124611f1c664daf196d4a927fec45b53",
    ),
    "reindex_workspace": (
        frozenset({"notes", "index"}),
        False,
        True,
        False,
        (),
        "c442004f00192b4054681ae30322f0a7a40a5782c13a9334562c4345a4539a4b",
    ),
}


async def test_notes_crud_tool_surface_unchanged(mcp_server):
    mcp, _ = mcp_server
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    assert set(tools) & set(EXPECTED) == set(EXPECTED), (
        "notes-CRUD tool set changed — a tool was renamed, dropped, or not mounted"
    )

    for name, (
        extra_tags,
        destructive,
        idempotent,
        read_only,
        params,
        description_sha,
    ) in EXPECTED.items():
        tool = tools[name]

        meta = tool.meta or {}
        fastmcp_meta = meta.get("fastmcp") or meta.get("_fastmcp") or {}
        tags = set(fastmcp_meta.get("tags") or [])
        expected_tags = extra_tags | {"read" if read_only else "write"}
        assert tags == expected_tags, f"{name}: tags {tags} != {expected_tags}"

        annotations = tool.annotations
        assert annotations is not None, f"{name}: missing annotations"
        assert annotations.read_only_hint is read_only, name
        assert annotations.destructive_hint is destructive, name
        assert annotations.idempotent_hint is idempotent, name
        assert annotations.open_world_hint is False, name

        properties = (tool.input_schema or {}).get("properties", {})
        assert "ws" not in properties, f"{name}: context dependency 'ws' leaked into schema"
        assert "ctx" not in properties, f"{name}: context dependency 'ctx' leaked into schema"
        actual_params = tuple(sorted(p for p in properties if p not in ("ws", "ctx")))
        assert actual_params == params, f"{name}: params {actual_params} != {params}"

        actual_sha = hashlib.sha256((tool.description or "").encode()).hexdigest()
        assert actual_sha == description_sha, (
            f"{name}: description changed (sha256 {actual_sha} != {description_sha})"
        )
