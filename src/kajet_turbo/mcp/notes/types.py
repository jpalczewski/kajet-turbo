from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from kajet_turbo.markdown import EditMode, EditSpec
from kajet_turbo.shared.notes import (
    DanglingLinkItem,
    FolderContext,
    NoteLinkItemWithMeta,
    NoteLinksBase,
    NoteListItem,
    TemporalWarning,
    WikilinkWarning,
)


class ToolInput(BaseModel):
    """Base for batch tool input items: an unknown key is an error, not a silent drop.

    A tool's own signature already rejects a misspelled parameter — pydantic's default
    would let the same typo pass unnoticed inside a batch item, which is the one place
    a caller cannot tell a dropped argument from an applied one.
    """

    model_config = ConfigDict(extra="forbid")


class NoteInput(ToolInput):
    title: str = Field(description="Note title; unique within (workspace, folder)")
    content: str = Field(
        default="",
        description="Markdown body; use [[Title]] or [[Folder/Title]] for wikilinks, "
        "[[note:ID]] for cross-workspace links",
    )
    tags: list[str] = Field(default=[], description="Tag list, e.g. ['work', 'work/projects']")
    folder: str = Field(
        default="",
        description="Folder path, e.g. 'Projects/Client A'; empty string = workspace root",
    )
    occurred_at: str | None = Field(
        default=None, description="Calendar date this note is about, formatted YYYY-MM-DD"
    )
    period: str | None = Field(
        default=None, description="Canonical period key, e.g. 2026-W12 or 2026-03"
    )


class SavedNoteResult(BaseModel):
    note_id: str
    warnings: list[WikilinkWarning] = Field(default_factory=list)
    occurred_at: str | None = None
    period: str | None = None


class ConflictItem(BaseModel):
    title: str
    folder: str


class MovedFolderResult(BaseModel):
    moved: int
    src: str
    dst: str


class FolderConflictResult(BaseModel):
    error: str
    conflicts: list[ConflictItem]


class DeletedNoteResult(BaseModel):
    note_id: str


class PrunedFoldersResult(BaseModel):
    pruned: list[str]
    count: int


class TagOperationResult(BaseModel):
    note_id: str
    tags: list[str]
    frontmatter_tags: list[str]
    warnings: list[str]


class TagRenameResult(BaseModel):
    old: str
    new: str
    renamed: int
    merged: bool
    inline_rewritten: int
    warnings: list[str]


class TagConflictResult(BaseModel):
    error: str
    target: str
    target_notes: int
    source_notes: int


class TagItem(BaseModel):
    path: str
    name: str
    count: int


class NoteListResponse(BaseModel):
    notes: list[NoteListItem]
    folder_context: FolderContext | None = Field(
        default=None,
        description="Metadata for the queried folder, present when a folder filter was given "
        "and metadata exists",
    )


class FolderInfo(BaseModel):
    """Folder with its description, returned by list_folders."""

    model_config = ConfigDict(from_attributes=True)

    path: str = Field(description="Folder path; empty string means workspace root")
    description: str = Field(description="What this folder is for; empty when not set")


class SearchChunkResult(BaseModel):
    note_id: str
    title: str
    folder: str
    updated_at: str
    header_path: list[str]
    content: str
    score: float
    matched_on: list[Literal["title", "tag", "folder"]] | None = Field(
        default=None,
        description=(
            "Non-null when this hit was surfaced by an exact metadata match "
            "(title/tag/folder), not only full-text/semantic ranking."
        ),
    )


class NoteLinkItem(NoteLinkItemWithMeta):
    # Redeclared with MCP-specific instructional wording — the shared base's descriptions
    # are kept REST-neutral since api/schemas/ imports it directly (see shared/notes.py).
    note_id: str = Field(
        description="Use in [[note:NOTE_ID]] to create a permanent cross-workspace link"
    )
    workspace: str | None = Field(
        default=None,
        description="Non-null and different from the note's own workspace means a "
        "cross-workspace link; reference with [[note:note_id]]",
    )


class NoteLinksResult(NoteLinksBase):
    # Redeclared, not inherited as-is: NoteLinksBase's fields are typed against the
    # shared (4-field) NoteLinkItem — pydantic bakes that type in at class-definition
    # time, so it wouldn't pick up this module's richer NoteLinkItem by itself.
    outlinks: list[NoteLinkItem]
    backlinks: list[NoteLinkItem]


class BatchNoteSuccess(BaseModel):
    index: int
    note_id: str
    warnings: list[WikilinkWarning] = Field(default_factory=list)


class BatchNoteError(BaseModel):
    index: int
    error: str


class StaleVersion(BaseModel):
    note_id: str
    error: str


class EditNoteSuccess(BaseModel):
    note_id: str
    replaced: int | None = None
    warnings: list[WikilinkWarning] = Field(default_factory=list)
    temporal_warnings: list[TemporalWarning] = Field(
        default_factory=list,
        description="occurred_at/period fields that had an unparseable value on disk "
        "(a hand edit, most often) and were kept at their previous value instead.",
    )
    occurred_at: str | None = None
    period: str | None = None


class GrepMatch(BaseModel):
    note_id: str
    title: str
    folder: str
    line_number: int
    line: str


class GrepResult(BaseModel):
    matches: list[GrepMatch]
    truncated: bool = Field(
        description="True if max_results was hit — more matches may exist beyond what's returned."
    )


class NoteReadError(BaseModel):
    note_id: str
    error: str


class OutlineSectionItem(BaseModel):
    level: int
    heading: str
    target_heading: str
    line_start: int
    line_end: int
    char_start: int
    char_end: int
    section_chars: int
    section_lines: int
    ambiguous: bool = Field(
        description="True when this heading text repeats elsewhere in the note — "
        "edit_note's target_heading lookup would raise an ambiguity error."
    )


class NoteOutlineResult(BaseModel):
    note_id: str
    title: str
    folder: str
    updated_at: str
    total_chars: int
    total_lines: int
    preamble_chars: int
    preamble_lines: int
    sections: list[OutlineSectionItem]


class OmittedNote(BaseModel):
    note_id: str
    title: str
    chars: int


class FolderExportResult(BaseModel):
    markdown: str
    note_count: int
    total_chars: int
    truncated: bool
    omitted: list[OmittedNote] = Field(
        description="Notes excluded once max_chars was hit (empty when nothing was truncated)."
    )


class NoteEditInput(ToolInput):
    note_id: str = Field(description="id of the note to edit")
    expected_sha: str = Field(
        description="The note's current HEAD sha from get_note/get_note_history — proof you "
        "saw this version before editing. A mismatch rejects the whole batch."
    )
    mode: EditMode = Field(
        default="append",
        description="As in edit_note. Defaults to 'append' (the least destructive) — in a "
        "batch, 'overwrite' across many notes at once is easy to get wrong.",
    )
    content: str | None = Field(
        default=None, description="Body text for the whole-body modes, as in edit_note."
    )
    target_heading: str | None = Field(
        default=None, description="Section heading for the section modes, as in edit_note."
    )
    old_str: str | None = Field(
        default=None, description="Anchor text for the text modes, as in edit_note."
    )
    new_str: str | None = Field(
        default=None, description="Replacement for old_str, as in edit_note."
    )
    replace_all: bool = False
    tags: list[str] | None = Field(
        default=None, description="Replaces this note's frontmatter tags; None = leave them."
    )
    occurred_at: str | None = Field(default=None, description="Calendar date this note is about")
    period: str | None = Field(default=None, description="Canonical period key this note covers")
    clear_date_metadata: bool = Field(
        default=False, description="Clear occurred_at and period; cannot be combined with either"
    )

    def to_edit_spec(self) -> EditSpec:
        """The mode-dependent edit payload, typed — batch bookkeeping (note_id,
        expected_sha, tags, ...) stays on this model, edit_many assembles the rest."""
        return EditSpec(
            mode=self.mode,
            content=self.content,
            old_str=self.old_str,
            new_str=self.new_str,
            target_heading=self.target_heading,
            replace_all=self.replace_all,
        )


class EditNotesSuccessItem(BaseModel):
    index: int
    note_id: str
    replaced: int | None = None
    warnings: list[WikilinkWarning] = Field(default_factory=list)
    temporal_warnings: list[TemporalWarning] = Field(default_factory=list)


class EditNotesApplied(BaseModel):
    applied: Literal[True]
    results: list[EditNotesSuccessItem]


class EditNotesError(BaseModel):
    index: int
    note_id: str
    error: str


class EditNotesRejected(BaseModel):
    applied: Literal[False]
    errors: list[EditNotesError] = Field(
        description="Cały batch odrzucony — nic nie zostało zapisane."
    )


class NoteDeleteInput(ToolInput):
    note_id: str = Field(description="id notatki do usunięcia")
    expected_sha: str = Field(
        description="Aktualny HEAD sha notatki z get_note_history — dowód, że przed "
        "usunięciem widziałeś bieżącą wersję. Niezgodność odrzuca cały batch."
    )


class DeleteNotesApplied(BaseModel):
    applied: Literal[True]
    results: list[BatchNoteSuccess]


class DeleteNotesError(BaseModel):
    index: int
    note_id: str
    error: str


class DeleteNotesRejected(BaseModel):
    applied: Literal[False]
    errors: list[DeleteNotesError] = Field(
        description="Cały batch odrzucony — nic nie zostało usunięte."
    )


# --- Graph (#168) ---
#
# MCP carries a leaner graph than REST: adjacency on the node instead of a parallel edge
# list. It halves the wire size of a dense page (77B -> 29B an edge), and it makes the
# paging invariant structural — a page cannot name an edge whose source it does not carry,
# because there is nowhere to write one. The REST shapes in `shared/notes.py` stay as they
# are; the frontend renders a whole graph at once and has no page to be consistent with.


class GraphNoteNode(NoteLinkItemWithMeta):
    """A note node in a graph page, carrying its own outgoing links."""

    kind: Literal["note"] = Field(description="Graph node kind")
    links: list[str] = Field(
        description="Ids this note links to: a note_id, or 'tag:<id>' for a tag hub. Only "
        "links whose source is on this page are listed, so walking every page yields each "
        "link exactly once; a link may name a node from another page."
    )


class GraphTagNode(BaseModel):
    """A workspace-scoped tag hub in a graph page."""

    id: str = Field(description="This tag's graph node id, of the form 'tag:<id>'")
    kind: Literal["tag"] = Field(description="Graph node kind")
    path: str = Field(description="Full normalized tag path")
    name: str = Field(description="Final segment of the tag path")
    workspace: str = Field(description="Workspace that owns this tag")
    links: list[str] = Field(description="Ids this hub links to — its parent tag, if any")


type GraphNode = GraphNoteNode | GraphTagNode


class GraphResult(BaseModel):
    """A page of a note-link graph."""

    nodes: list[GraphNode] = Field(
        description="This page's nodes; get_workspace_graph ranks them highest-degree first"
    )
    total_nodes: int = Field(description="Note nodes in the whole graph, tag hubs excluded")
    total_edges: int = Field(
        description="Links between note nodes in the whole graph; tag hubs and the links "
        "into them are not counted."
    )
    next_offset: int | None = Field(
        description="Pass as offset for the next page; null means this was the last one."
    )
    dangling_links: list[DanglingLinkItem] | None = Field(
        default=None,
        description="Wikilinks on this page's notes that resolve to nothing. Null when the "
        "workspace validates links (a broken one cannot be saved, so none are tracked).",
    )

    @model_validator(mode="before")
    @classmethod
    def _fold_edges_into_nodes(cls, data: Any, info: ValidationInfo) -> Any:
        """Fold the service's REST-shaped ``edges`` list into per-node ``links``.

        The service keeps producing the edge-list shape because the REST route serializes
        its dict verbatim; only this model pays for the leaner form. An unpaged caller
        (`get_note_neighborhood`) hands over no totals, so they are derived from what it
        did hand over — one page holding everything — which keeps the fields required
        rather than optional in the schema the calling model reads.

        `workspace` on a same-workspace note is dropped to null: `NoteLinkItem` already
        documents non-null-and-different as the cross-workspace signal, so null is the
        documented reading of "here", not a lossy shortcut.
        """
        if not isinstance(data, dict) or "edges" not in data:
            return data
        adjacency: dict[str, list[str]] = {}
        for edge in data["edges"]:
            adjacency.setdefault(edge["source"], []).append(edge["target"])
        home = (info.context or {}).get("workspace")
        nodes = [dict(node) for node in data["nodes"]]
        note_ids = set()
        for node in nodes:
            if node["kind"] == "note":
                node_id = node.pop("id")
                note_ids.add(node_id)
                if home is not None and node.get("workspace") == home:
                    node["workspace"] = None
            else:
                node_id = node["id"]
            node["links"] = adjacency.get(node_id, [])
        folded = {key: value for key, value in data.items() if key != "edges"}
        folded["nodes"] = nodes
        folded.setdefault("total_nodes", len(note_ids))
        # Note-to-note only, matching what the paged path counts before `_graph_tags`
        # appends its edges — a tag edge has a note as its source but never as its target.
        folded.setdefault(
            "total_edges",
            sum(
                1
                for edge in data["edges"]
                if edge["source"] in note_ids and edge["target"] in note_ids
            ),
        )
        folded.setdefault("next_offset", None)
        return folded
