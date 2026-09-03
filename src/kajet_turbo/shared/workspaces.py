"""Wire types shared between the REST API (`api/schemas/workspaces/`) and MCP
(`mcp/workspaces/`) layers. See `shared/notes.py` for the leaf-module rule this follows.
"""

from pydantic import BaseModel, Field


class WorkspaceInfoBase(BaseModel):
    """A workspace's identity and description."""

    name: str = Field(description="Workspace name")
    description: str = Field(default="", description="What this workspace is for")
    folder: str = Field(
        default="", description="Folder path for grouping this workspace in the picker"
    )
    tags: list[str] = Field(default_factory=list, description="Tags on this workspace")
