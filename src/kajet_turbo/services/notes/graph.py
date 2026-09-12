import json
from collections import Counter
from collections.abc import Callable
from itertools import chain

from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.notes import NoteLinkRepository, NoteRepository, NoteTagRepository
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget


def resolve_link_notes(
    crud_repo: NoteRepository,
    note_ids: list[str],
    owner_id: str,
    include_meta: bool = False,
) -> list[dict]:
    """Map note ids to link payloads, preserving their requested order and skipping misses."""
    notes = {note.id: note for note in crud_repo.get_many(note_ids, owner_id)}
    result = []
    for note_id in note_ids:
        note = notes.get(note_id)
        if note is None:
            continue
        entry: dict = {
            "note_id": note.id,
            "title": note.title,
            "folder": note.folder,
            "workspace": note.workspace,
        }
        if include_meta:
            entry["tags"] = json.loads(note.tags or "[]")
            entry["updated_at"] = note.updated_at
        result.append(entry)
    return result


class NoteGraphService:
    """Read-only whole-workspace and neighborhood link-graph assembly."""

    def __init__(
        self,
        crud_repo: NoteRepository,
        link_repo: NoteLinkRepository,
        tag_repo: NoteTagRepository,
        dangling_repo: DanglingLinkRepository | None,
        link_validation_enabled: Callable[[str, str], bool] | None,
    ):
        self._crud_repo = crud_repo
        self._link_repo = link_repo
        self._tag_repo = tag_repo
        self._dangling_repo = dangling_repo
        self._link_validation_enabled = link_validation_enabled

    def _links_validated(self, ws_name: str, owner_id: str) -> bool:
        if self._link_validation_enabled is None:
            return True
        return self._link_validation_enabled(ws_name, owner_id)

    def graph(
        self,
        target: WorkspaceTarget,
        include_tags: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict:
        """Whole-workspace note-link graph, including isolated notes and dangling links."""
        edges = self._link_repo.list_for_workspace(target.name, target.owner_id)
        # Every edge's source is already in list_paths (NoteLink.workspace is always the
        # source's own workspace), but a cross-workspace [[note:ID]] target may not be.
        node_ids = {n.note_id for n in self._crud_repo.list_paths(target.name, target.owner_id)}
        node_ids.update(t for _, t in edges)
        degree = Counter(chain.from_iterable(edges))
        return self._build_graph(
            sorted(node_ids, key=lambda note_id: (-degree[note_id], note_id)),
            edges,
            target.owner_id,
            target.name,
            include_tags=include_tags,
            limit=limit,
            offset=offset,
        )

    def neighborhood(
        self,
        target: NoteTarget,
        depth: int = 2,
        include_cross_workspace: bool = False,
        include_tags: bool = False,
    ) -> dict | None:
        """The directed induced graph within an undirected N-hop radius of ``note_id``."""
        center = self._crud_repo.get(target.note_id, owner_id=target.workspace.owner_id)
        if center is None or center.workspace != target.workspace.name:
            return None
        edges = self._link_repo.neighborhood(
            target.note_id,
            target.workspace.name,
            target.workspace.owner_id,
            depth,
            include_cross_workspace=include_cross_workspace,
        )
        node_ids = {target.note_id}
        node_ids.update(source for source, _ in edges)
        node_ids.update(target for _, target in edges)
        return self._build_graph(
            sorted(node_ids),
            edges,
            target.workspace.owner_id,
            target.workspace.name,
            dangling_source_ids=node_ids,
            include_tags=include_tags,
        )

    def _build_graph(
        self,
        node_ids: list[str],
        edges: list[tuple[str, str]],
        owner_id: str,
        ws_name: str,
        *,
        dangling_source_ids: set[str] | None = None,
        include_tags: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict:
        """Assemble graph nodes, edges, optional tag hubs, and dangling-link payloads."""
        nodes = resolve_link_notes(self._crud_repo, node_ids, owner_id, include_meta=True)
        for node in nodes:
            node["id"] = node["note_id"]
            node["kind"] = "note"
        resolved_ids = {n["note_id"] for n in nodes}
        filtered_edges = [(s, t) for s, t in edges if s in resolved_ids and t in resolved_ids]
        result: dict = {}
        if limit is not None:
            next_offset = offset + limit
            result = {
                "total_nodes": len(nodes),
                "total_edges": len(filtered_edges),
                "next_offset": next_offset if next_offset < len(nodes) else None,
            }
            nodes = nodes[offset:next_offset]
            page_ids = {n["note_id"] for n in nodes}
            dangling_source_ids = (
                page_ids if dangling_source_ids is None else dangling_source_ids & page_ids
            )
        else:
            page_ids = resolved_ids
        graph_edges = [
            {"source": source, "target": target}
            for source, target in sorted(filtered_edges)
            if source in page_ids
        ]
        if include_tags:
            tag_nodes, tag_edges = self._graph_tags(nodes, owner_id)
            nodes.extend(tag_nodes)
            graph_edges.extend(tag_edges)
        result["nodes"] = nodes
        result["edges"] = sorted(graph_edges, key=lambda edge: (edge["source"], edge["target"]))
        if self._dangling_repo is not None and not self._links_validated(ws_name, owner_id):
            dangling_rows = self._dangling_repo.list_for_workspace(owner_id, ws_name)
            if dangling_source_ids is not None:
                dangling_rows = [
                    row for row in dangling_rows if row["source_note_id"] in dangling_source_ids
                ]
            result["dangling_links"] = [
                {
                    "source_note_id": row["source_note_id"],
                    "target_folder": row["target_folder"],
                    "target_title": row["target_title"],
                }
                for row in dangling_rows
            ]
        else:
            result["dangling_links"] = None
        return result

    def _graph_tags(self, note_nodes: list[dict], owner_id: str) -> tuple[list[dict], list[dict]]:
        """Build direct note-tag and child-parent tag edges for resolved graph notes."""
        note_ids = {node["note_id"] for node in note_nodes}
        workspaces = {node["workspace"] for node in note_nodes}
        tags, assignments = self._tag_repo.graph_data(owner_id, workspaces, note_ids)
        by_id = {tag.id: tag for tag in tags}
        included_ids = {tag_id for _, tag_id in assignments}
        pending = list(included_ids)
        while pending:
            tag = by_id[pending.pop()]
            if tag.parent_id is not None and tag.parent_id not in included_ids:
                included_ids.add(tag.parent_id)
                pending.append(tag.parent_id)

        def graph_id(tag_id: str) -> str:
            return f"tag:{tag_id}"

        tag_nodes = [
            {
                "id": graph_id(tag.id),
                "kind": "tag",
                "path": tag.path,
                "name": tag.name,
                "workspace": tag.workspace,
            }
            for tag_id in sorted(
                included_ids, key=lambda item: (by_id[item].workspace, by_id[item].path)
            )
            if (tag := by_id[tag_id])
        ]
        tag_edges = [
            {"source": note_id, "target": graph_id(tag_id)} for note_id, tag_id in assignments
        ]
        tag_edges.extend(
            {"source": graph_id(tag.id), "target": graph_id(tag.parent_id)}
            for tag_id in included_ids
            if (tag := by_id[tag_id]).parent_id is not None
        )
        return tag_nodes, tag_edges
