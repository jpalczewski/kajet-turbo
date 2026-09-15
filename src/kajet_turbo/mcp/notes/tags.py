from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from kajet_turbo.concurrency import run_sync
from kajet_turbo.mcp.context import (
    NOTE_TARGET_WRITE,
    WORKSPACE_TARGET,
    WORKSPACE_TARGET_WRITE,
)
from kajet_turbo.mcp.notes.types import (
    StaleVersion,
    TagConflictResult,
    TagItem,
    TagOperationResult,
    TagRenameResult,
)
from kajet_turbo.mcp.tooling import (
    publish_note_updated,
    publish_workspace_changed,
    read_tool,
    write_tool,
)
from kajet_turbo.services.notes import NoteTagService
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget
from kajet_turbo.services.workspaces import WorkspaceService


def build_tags(
    tag_service: NoteTagService,
    workspace_service: WorkspaceService,
) -> FastMCP:
    srv = FastMCP("notes-tags")

    @srv.tool(**write_tool(tags={"notes", "tags"}, idempotent=True))
    async def add_tag(
        note_id: str,
        tags: list[str],
        target: NoteTarget = NOTE_TARGET_WRITE,
    ) -> TagOperationResult:
        """Adds tags to the note's frontmatter (idempotently), without touching content.
        Note: this only touches frontmatter tags; inline #hashtags live in the content."""
        result = await run_sync(tag_service.add_tags, target, tags)
        if result["changed"]:
            await publish_note_updated(target.workspace, note_id)
        return TagOperationResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "tags"}, idempotent=True))
    async def remove_tag(
        note_id: str,
        tags: list[str],
        target: NoteTarget = NOTE_TARGET_WRITE,
    ) -> TagOperationResult:
        """Removes tags from the note's frontmatter (idempotently), without touching content.
        A tag present only as an inline #hashtag will not disappear — it comes back as a warning."""
        result = await run_sync(tag_service.remove_tags, target, tags)
        if result["changed"]:
            await publish_note_updated(target.workspace, note_id)
        return TagOperationResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "tags"}, destructive=True))
    async def set_tags(
        note_id: str,
        tags: list[str],
        expected_sha: Annotated[
            str,
            Field(
                description="The note's current HEAD sha from get_note/get_note_history — "
                "proof you saw the current version before overwriting tags. A mismatch "
                "returns StaleVersion."
            ),
        ],
        target: NoteTarget = NOTE_TARGET_WRITE,
    ) -> TagOperationResult | StaleVersion:
        """Overwrites the note's tag frontmatter with the given list, without touching content.
        Destructive (can remove existing tags) — requires expected_sha from
        get_note/get_note_history; a mismatch returns StaleVersion — re-read the note and
        retry with the fresh sha.
        Success: TagOperationResult {note_id, tags, frontmatter_tags, warnings}."""
        result = await run_sync(tag_service.set_tags, target, tags, expected_sha)
        if result.get("stale_sha"):
            return StaleVersion.model_validate(result)
        if result["changed"]:
            await publish_note_updated(target.workspace, note_id)
        return TagOperationResult.model_validate(result)

    @srv.tool(**write_tool(tags={"notes", "tags"}, idempotent=True))
    async def rename_tag(
        old: str,
        new: str,
        workspace: str,
        merge: Annotated[
            bool,
            Field(
                description="Consent to merge when tag `new` already exists. Without it, "
                "that case returns TagConflictResult instead of changing anything."
            ),
        ] = False,
        target: WorkspaceTarget = WORKSPACE_TARGET_WRITE,
    ) -> TagRenameResult | TagConflictResult:
        """Renames a tag across the whole workspace, instead of N x set_tags calls. Takes
        the whole subtree: 'work' -> 'job' also rewrites 'work/projects' (matched on
        segment boundaries, so 'workflow' is left alone). Also rewrites inline #hashtags in
        note bodies — otherwise the old tag would come back on the next sync.
        workspace: the workspace name to operate in.
        When `new` already exists, this is a merge — requires merge=true, otherwise returns
        TagConflictResult with the note count on each side.
        No expected_sha (this is workspace-wide) — roll back via git history. A rename over
        ~500 notes lands as several commits, not one: if it fails partway through, the
        already-renamed notes already carry the target tag, so retrying needs merge=true too.
        Search indexing (chunks/FTS/embeddings) is deferred to background jobs for every
        note whose body was rewritten."""
        result = await run_sync(
            tag_service.rename_tag,
            old,
            new,
            target,
            merge=merge,
        )
        if result.get("error"):
            return TagConflictResult.model_validate(result)
        if result["renamed"]:
            await publish_workspace_changed(target)
        return TagRenameResult.model_validate(result)

    @srv.tool(**read_tool(tags={"notes", "tags"}))
    async def list_tags(
        workspace: str,
        folder: Annotated[
            str | None,
            Field(
                description="Optional filter — count only tags of notes in this folder "
                "(e.g. 'Projects/Client A'). Omit to use the whole workspace."
            ),
        ] = None,
        include_subfolders: Annotated[
            bool,
            Field(description="With folder set: whether to include subfolders (default yes)."),
        ] = True,
        target: WorkspaceTarget = WORKSPACE_TARGET,
    ) -> list[TagItem]:
        """Returns the tags of a workspace with popularity counts, sorted descending by
        note count. Each item: {path, name, count}.
        workspace: the workspace name to operate in.
        Use this to survey existing tags before tagging — optionally narrowed to a
        folder."""
        tags_result = await run_sync(
            tag_service.tag_counts,
            target.name,
            owner_id=target.owner_id,
            folder=folder,
            include_subfolders=include_subfolders,
        )
        return [TagItem.model_validate(t) for t in tags_result]

    return srv
