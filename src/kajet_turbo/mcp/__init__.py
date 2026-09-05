from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore

from kajet_turbo.mcp.collections import build_collections
from kajet_turbo.mcp.context import build_mcp_context
from kajet_turbo.mcp.notes import build_notes
from kajet_turbo.mcp.tooling import ServiceErrorMiddleware
from kajet_turbo.mcp.workspaces import build_workspaces

if TYPE_CHECKING:
    from kajet_turbo.dependencies import AppResources

_INSTRUCTIONS = """
Kajet — git-versioned markdown notebook.

## Workflow
1. list_workspaces → activate_workspace before operations that use the active workspace
2. search_notes(workspace="all") or search_notes(workspace="NAME") can be used without
   activation; "all" omits workspaces whose global-search setting is disabled
3. list_folders / search_notes / list_notes to orient yourself
4. get_note / save_note / edit_note / save_notes for reads and writes

## Wikilink syntax (use in note content)
- [[Title]] — link to a note by title, found anywhere in the workspace
- [[Folder/SubFolder/Title]] — link by folder path + title; the path is a suffix, so
  [[SubFolder/Title]] also matches Folder/SubFolder/Title
- [[Target|Displayed text]] — link with display alias
- Same title in several folders: the exact full path wins, otherwise the note nearest
  the linking note (same folder, then closest ancestor). Save/edit responses report this
  as an ambiguous_wikilink warning; use the full path to make the target explicit.
- Case-insensitive fallback: if no note's title matches exactly, matching retries
  ignoring letter case — [[plan projektu]] resolves to a note titled "Plan projektu"
  when no note is titled exactly "plan projektu". The link still resolves, but the
  response carries a case_corrected_wikilink warning naming the real title; fix the
  link text to match it. get_note(title=...) is the one exception — it stays exact/
  case-sensitive always, so a case-mismatched title there returns a plain not-found
  error instead of guessing.
- [[note:NOTE_ID]] — cross-workspace permanent link; NOTE_ID is the note_id UUID
  from any note response; renders as a clickable link to the note in its workspace

Use [[note:NOTE_ID]] when linking across workspaces — the title-based forms only
resolve within the active workspace.

## Identifiers
- note_id: stable UUID — use for get_note, edit_note, delete_note, get_note_links
- (folder, title): natural key — unique per workspace; folder is "" for workspace root
- Folder paths: slash-separated, e.g. "Projects/Client A"; "" = workspace root

## Folders
- list_folders returns folders with optional description
- list_notes with folder= filter returns folder_context.instructions if set — follow them
- set_folder_meta sets per-folder description and LLM instructions

## Read ergonomics
- grep_notes — literal substring search with line numbers; use instead of search_notes when
  you need exact-text certainty (search_notes ranks semantically/FTS, no literal guarantee)
- get_note(title=..., folder=...) — address a note by its title instead of its UUID;
  folder is a path *suffix* like a wikilink, omit it to search the whole workspace.
  An ambiguous title errors with the candidates rather than guessing
- get_notes — read many note_ids in one call instead of N x get_note
- get_note_outline → edit_note(target_heading=...) — inspect a note's headings without
  loading its body, then edit just that section surgically
- export_folder — concatenate a folder's subtree into one markdown corpus, for
  reconnaissance across many related notes instead of N x get_note
- entries_in(period=..., folder=...) — notes whose date falls in a calendar period
  (year/month/ISO week/day), e.g. "what happened in 2026-W12"; use instead of
  list_notes/search_notes for date-range questions

## Tags
- Tags are hierarchical slash-paths ("work/projects"); list_notes/search_notes filters match
  by segment prefix, so tags= ["work"] also returns notes tagged "work/projects"
- rename_tag — rename a tag across the whole workspace instead of N x set_tags. Takes the
  subtree with it and rewrites inline #hashtags in note bodies. Renaming onto an existing tag
  is a merge and needs merge=true; otherwise it reports the conflict untouched. A rename over
  ~500 notes lands as several git commits, not one — a mid-rename failure needs a merge=true
  retry, same as renaming onto an existing tag

## Batch editing
- edit_note(replace_all=true) — replace_text/delete_text on every match in one note
  instead of requiring old_str to be unique; response carries replaced with the count
- edit_notes — edit multiple existing notes in one atomic commit, all-or-nothing: any
  invalid item rejects the whole batch before anything is written. Content + tags only,
  no renames (use edit_note for that); unlike save_notes, never leaves a partial batch
- delete_notes — delete multiple notes in one Git commit and one DB transaction, all-or-
  nothing at validation: any invalid item rejects the whole batch before anything is
  deleted. A stale expected_sha rejects the whole batch; call get_note_history to refresh
  before retrying

## Destructive operations
Every destructive per-note operation (edit_note, edit_notes, set_tags, delete_note,
delete_notes, restore_note_version) requires expected_sha — the note's current HEAD sha
from get_note/get_note_history — proving you read the version you are about to change.
A mismatch returns StaleVersion (or rejects the batch): re-read the note and retry with
the fresh sha. There is no confirm flag; git history (restore_note_version) is the undo.
"""


def build_mcp(resources: AppResources | Any, *legacy: object) -> FastMCP:
    """Build MCP from one application graph.

    The compatibility branch keeps isolated unit fixtures working while they move to
    AppResources; production callers must pass a single resource graph.
    """
    if legacy:
        note_service = resources
        (
            workspace_service,
            folder_meta_repo,
            oauth_repo,
            active_workspace_repo,
            provider,
            collection_service,
        ) = cast(tuple[Any, Any, Any, Any, Any, Any], legacy)
        from kajet_turbo.repositories.events import EventRepository

        resources = SimpleNamespace(
            note_service=note_service,
            workspace_service=workspace_service,
            folder_meta_repo=folder_meta_repo,
            oauth_repo=oauth_repo,
            active_workspace_repo=active_workspace_repo,
            provider=provider,
            collection_service=collection_service,
            event_repo=EventRepository(oauth_repo._engine),
        )
    state_store = MemoryStore()
    context = build_mcp_context(
        resources.workspace_service,
        resources.oauth_repo,
        resources.active_workspace_repo,
        resources.event_repo,
    )
    mcp = FastMCP(
        "kajet-turbo",
        instructions=_INSTRUCTIONS,
        auth=resources.provider,
        session_state_store=state_store,
    )
    mcp.add_middleware(ServiceErrorMiddleware(context))
    mcp.mount(
        build_workspaces(
            resources.workspace_service, resources.active_workspace_repo, state_store=state_store
        )
    )
    mcp.mount(
        build_notes(
            resources.note_service,
            resources.workspace_service,
            resources.folder_meta_repo,
            resources.collection_service,
            state_store=state_store,
        )
    )
    mcp.mount(
        build_collections(
            resources.collection_service, resources.workspace_service, state_store=state_store
        )
    )
    return mcp
