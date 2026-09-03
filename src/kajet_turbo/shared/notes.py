"""Wire types shared between the REST API (`api/schemas/`) and MCP (`mcp/notes/`) layers.

Leaf module: only `pydantic`/`typing` imports allowed here, never `kajet_turbo.*` — both
layers import from this module, so an import the other way would create a cycle.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WikilinkWarning(BaseModel):
    """A wikilink that resolved ambiguously or via case-insensitive fallback."""

    kind: Literal["ambiguous_wikilink", "case_corrected_wikilink"] = Field(
        description="Which kind of non-exact resolution happened"
    )
    target: str = Field(description="The raw wikilink target text as written")
    resolved_to: str = Field(description="folder/title path of the note it resolved to")
    alternatives: list[str] = Field(
        default_factory=list, description="Other candidate notes, when the match was ambiguous"
    )


class NoteLinkItem(BaseModel):
    """A note referenced by a link, identified and located for the caller.

    Field descriptions here are kept REST-neutral (no wikilink-authoring instructions) —
    this class is imported as-is by api/schemas/, not just subclassed. MCP-specific
    instructional wording (e.g. how to write a [[note:ID]] link) belongs on a subclass
    in mcp/notes/types.py, not here.
    """

    note_id: str = Field(description="Note id")
    title: str = Field(description="Note title")
    folder: str = Field(description="Folder path; empty string means workspace root")
    workspace: str | None = Field(
        default=None,
        description="Non-null and different from the caller's active workspace means this "
        "is a cross-workspace reference",
    )


class MovedNoteResult(BaseModel):
    """Result of moving a note to a new folder."""

    note_id: str = Field(description="The moved note's id")
    folder: str = Field(description="The note's new folder path")


class HistoryEntry(BaseModel):
    """One git commit in a note's version history."""

    sha: str = Field(description="Commit sha, full or short")
    message: str = Field(description="Commit message")
    timestamp: int = Field(description="Commit time, unix epoch seconds")


class ReindexResult(BaseModel):
    """Result of a manual reindex request."""

    message: str = Field(description="Human-readable summary")
    count: int = Field(description="Number of notes marked for reindexing")


class FolderContext(BaseModel):
    """Metadata for a folder, surfaced alongside a note listing."""

    model_config = ConfigDict(from_attributes=True)

    path: str = Field(description="Folder path; empty string means workspace root")
    description: str = Field(description="What this folder is for")
    instructions: str = Field(description="LLM instructions for working with notes in this folder")


class NoteListItem(BaseModel):
    """A note's identity and metadata as returned by a listing/search endpoint."""

    note_id: str = Field(description="Note id")
    workspace: str = Field(description="Owning workspace name")
    owner_id: str = Field(description="Owning user id")
    title: str = Field(description="Note title")
    folder: str = Field(description="Folder path; empty string means workspace root")
    tags: list[str] = Field(description="Tag paths attached to this note")
    created_at: str = Field(description="Creation timestamp, ISO 8601")
    updated_at: str = Field(description="Last-modified timestamp, ISO 8601")
    occurred_at: str | None = Field(default=None, description="Calendar date this note is about")
    period: str | None = Field(default=None, description="Canonical period key, e.g. 2026-W12")


class NoteLinksBase(BaseModel):
    """A note's outgoing and incoming wikilinks.

    Gotcha for any subclass that needs a richer item type than the plain `NoteLinkItem`
    above (e.g. one with `tags`/`updated_at` attached): pydantic bakes a field's type in
    at class-definition time, so `class X(NoteLinksBase): pass` does NOT pick up a
    subclass's own richer `NoteLinkItem` just because it's in scope — `outlinks`/
    `backlinks` stay typed against this module's plain `NoteLinkItem`. A subclass using a
    different item type must redeclare both fields with that type explicitly.
    """

    outlinks: list[NoteLinkItem] = Field(description="Notes this note links to")
    backlinks: list[NoteLinkItem] = Field(description="Notes that link to this note")


class NoteLinkItemWithMeta(NoteLinkItem):
    """`NoteLinkItem` plus list metadata (`tags`/`updated_at`) — shared by every surface
    that shows note-link items alongside a note listing (get_note_links with
    include_meta=True, the workspace graph)."""

    tags: list[str] | None = None
    updated_at: str | None = None


# --- Whole-workspace graph (#133) ---


class GraphNode(NoteLinkItemWithMeta):
    """A note as a node in the workspace link graph, with list metadata attached."""


class GraphEdge(BaseModel):
    """A resolved wikilink edge in the workspace link graph."""

    source: str = Field(description="Source note id")
    target: str = Field(description="Target note id")


class DanglingLinkItem(BaseModel):
    """An unresolved wikilink target, tracked only when link validation is off."""

    source_note_id: str = Field(description="The note containing the broken link")
    target_folder: str = Field(description="Folder the link target was written against")
    target_title: str = Field(description="Title the link couldn't resolve to a note")


class GraphBase(BaseModel):
    """Whole-workspace note-link graph: every note as a node, every resolved wikilink as
    an edge, and broken/dangling links when the workspace has link validation disabled."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    dangling_links: list[DanglingLinkItem] | None = None
