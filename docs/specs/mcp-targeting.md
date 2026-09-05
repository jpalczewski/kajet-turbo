# MCP targeting baseline

Baseline recorded 2026-09-05 for the FastMCP 4 migration tracked by #237. This is
an inventory of the pre-migration catalog, not the post-migration API contract.

## Addressing rules

| Rule | Meaning after #248 |
| --- | --- |
| note ID | Resolve the note's owner, workspace, and path from `note_id`, then authorize. |
| explicit workspace | Require a workspace target; no active-workspace fallback. |
| search | Search all eligible workspaces by default; `workspace` optionally narrows it. |
| batch by IDs | Resolve each ID independently; mixed workspace rules are enforced by the operation. |
| batch explicit workspace | One explicitly selected workspace applies to all batch items. |
| workspace management | Uses its existing explicit `name` parameter, or is not workspace-targeted. |

## Registered tool inventory

Every `@srv.tool` registration under `src/kajet_turbo/mcp` appears exactly once.

| Tool | Rule |
| --- | --- |
| `get_note` | note ID when `note_id` is supplied; explicit workspace when addressed by `title`/`folder` |
| `get_note_outline`, `get_note_history`, `get_note_at_version`, `restore_note_version`, `get_note_links`, `edit_note`, `move_note`, `delete_note`, `add_tag`, `remove_tag`, `set_tags` | note ID |
| `get_notes`, `edit_notes`, `delete_notes` | batch by IDs |
| `save_notes` | batch explicit workspace |
| `search_notes` | search |
| `save_note`, `grep_notes`, `list_notes`, `export_folder`, `list_folders`, `set_folder_meta`, `move_folder`, `rename_folder`, `prune_empty_folders`, `get_workspace_graph`, `entries_in`, `rename_tag`, `list_tags`, `reindex_workspace`, `define_collection`, `delete_collection`, `list_collections`, `open_entry`, `list_collection_entries` | explicit workspace |
| `list_workspaces`, `activate_workspace`, `create_workspace`, `update_workspace`, `list_workspace_settings`, `set_workspace_setting` | workspace management |

`get_note` has two mutually exclusive addressing modes, as specified in #237; that
is one tool with a defined rule for each legal input combination, rather than two
registrations. `activate_workspace` is included because it remains registered in
this baseline and will be removed by #248.

## Client and protocol observations

Source: anonymised production `mcp_request_headers` records in
`ops/logs/produkcja_mcp_20260627-174354.log`, observed 2026-06-27. The committed
sample identifies only Claude.ai (`x-anthropic-client: ClaudeAI`); it does not
contain an independently identifiable Claude Code request, so its current client
version and negotiated revision must be captured before #244 changes transport.

| Required client | Client version | `MCP-Protocol-Version` | Echoes `Mcp-Session-Id` | Evidence |
| --- | --- | --- | --- | --- |
| claude.ai | not logged | `2025-11-25` | Mixed: most later calls echo it; a request in the same sample omits it | `Claude-User`, `ClaudeAI`; session IDs redacted from this document |
| Claude Code | not present in retained sample | pending capture | pending capture | Required by #237, no claim without an observation |

The claude.ai omission is the baseline that exercises the per-user fallback in the
two strict-xfail regressions. The raw sample is not reproduced here because request
headers include identifiers and tracing metadata.

## Dependency and runtime baseline

`uv.lock` at this baseline pins:

| Dependency | Version |
| --- | --- |
| FastMCP | 3.4.2 |
| MCP SDK | 1.29.0 |
| FastAPI | 0.136.3 |
| Starlette | 1.3.1 |
| Pydantic | 2.13.4 |
| AnyIO | 4.13.0 |

On 2026-09-05, using the deployment-compatible local interpreter (`CPython 3.14t`),
`MCP_BASE_URL=http://localhost:8000 DB_PATH=/private/tmp/kajet-243-gil.db uv run
python -c 'import sys; import kajet_turbo.server; print(sys._is_gil_enabled())'`
printed `False`. The full application import therefore retained disabled-GIL mode
for this dependency baseline. Repeat this check on the deployment image after the
FastMCP upgrade.
