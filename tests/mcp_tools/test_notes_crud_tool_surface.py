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
        ("content", "extras", "folder", "occurred_at", "period", "tags", "title", "workspace"),
        "2eedacd5d86537f3a772a03c917863788755d77809374ca37e598703a685894c",
    ),
    "save_notes": (
        frozenset({"notes", "crud"}),
        False,
        False,
        False,
        ("notes", "workspace"),
        "6266e1314064cba6a3f8db73e4493fc0eff7c4d5f5cacaf9298ad1caa0dbd5d1",
    ),
    "get_note": (
        frozenset({"notes", "crud"}),
        False,
        True,
        True,
        ("folder", "note_id", "title", "workspace"),
        "23a9937991a3a3d7c8fabbc8306fd044d9976d8365b4e79bb538f62b28fdad81",
    ),
    "get_notes": (
        frozenset({"notes", "crud"}),
        False,
        True,
        True,
        ("note_ids",),
        "63ccc11c917ebc28cc15cc12d509711f6260df89ca9fb526cc4309658a8b196b",
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
            "extras",
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
        "f93b21a2d62d7d6de4888049ac48e501f640ca9b8cc7ecf069937429e902aae1",
    ),
    "move_note": (
        frozenset({"notes", "crud"}),
        False,
        False,
        False,
        ("folder", "note_id"),
        "2b343a735aa3880a1d652dc0bdd4669ca2fa7499b15096ec13e5c45223f37416",
    ),
    "delete_note": (
        frozenset({"notes", "crud"}),
        True,
        False,
        False,
        ("expected_sha", "note_id"),
        "9e38d5d5f219493c9836005039e3820b2364d24ffab35106bfd94acdcccc3804",
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
        ("folder", "limit", "sort", "tags", "workspace"),
        "20e8ca2c2175856335d2bd0b74ba5801c508313ebf13bed1ea7e4b9f0356a0ec",
    ),
    "entries_in": (
        frozenset({"notes", "crud"}),
        False,
        True,
        True,
        ("collection", "folder", "period", "workspace"),
        "22fcc4b38d4021526fd2cd4f4fccb43e5012000f707b6c387f3fc994ee5b9ff8",
    ),
    "export_folder": (
        frozenset({"notes", "crud"}),
        False,
        True,
        True,
        ("folder", "max_chars", "workspace"),
        "2003952aadb3d7f3a3eb5b0ef9cafbd27162c046184a626256b03c1fb58d534b",
    ),
    "search_notes": (
        frozenset({"notes", "search"}),
        False,
        True,
        True,
        ("folder", "limit", "query", "tags", "workspace"),
        "2cf275a8d7d5c5d766a3e4f255645184fec25384eed88b591138cfff013bdebe",
    ),
    "grep_notes": (
        frozenset({"notes", "search"}),
        False,
        True,
        True,
        ("case_sensitive", "folder", "max_results", "pattern", "workspace"),
        "001b96ff5f4556a1f9232b9fe6087bcf4d1dd53c76ac75b264053ed3b6a25865",
    ),
    "reindex_workspace": (
        frozenset({"notes", "index"}),
        False,
        True,
        False,
        ("workspace",),
        "cf722f8639dfd2b8141854712544868a81e82d279ae30e3425fbfa745578f4f3",
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
