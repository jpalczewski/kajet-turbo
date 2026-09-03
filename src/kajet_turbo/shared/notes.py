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
    """A note referenced by a link, identified and located for the caller."""

    note_id: str = Field(
        description="Use in [[note:NOTE_ID]] to create a permanent cross-workspace link"
    )
    title: str = Field(description="Note title")
    folder: str = Field(description="Folder path; empty string means workspace root")
    workspace: str | None = Field(
        default=None,
        description="Non-null and != active workspace means cross-workspace link; reference "
        "with [[note:note_id]]",
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
    """A note's outgoing and incoming wikilinks."""

    outlinks: list[NoteLinkItem] = Field(description="Notes this note links to")
    backlinks: list[NoteLinkItem] = Field(description="Notes that link to this note")


# --- Whole-workspace graph (#133) ---


class GraphNode(NoteLinkItem):
    """A note as a node in the workspace link graph, with list metadata attached."""

    tags: list[str] | None = None
    updated_at: str | None = None


class GraphEdge(BaseModel):
    """A resolved wikilink edge in the workspace link graph."""

    source: str = Field(description="Source note id")
    target: str = Field(description="Target note id")


class DanglingLinkItem(BaseModel):
    """An unresolved wikilink target, tracked only when link validation is off."""

    source_note_id: str = Field(description="The note containing the broken link")
    target_folder: str = Field(description="Folder the link target was written against")
    target_title: str = Field(description="Title the link couldn't resolve to a note")
