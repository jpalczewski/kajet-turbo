import Graph from 'graphology';
import type { DanglingLinkItem, GraphNode, GraphResponse } from '$lib/api';

export type NodeKind = 'note' | 'tag' | 'dangling';

/** A dangling/broken wikilink target, synthesized as a graph node — the backend only reports
 * these as flat (source, target) pairs, not as graph nodes (see GraphResponse.dangling_links). */
export interface DanglingGraphNode {
  kind: 'dangling';
  id: string;
  targetFolder: string;
  targetTitle: string;
}

export type AnyGraphNode = GraphNode | DanglingGraphNode;

export interface GraphNodeAttributes {
  source: AnyGraphNode;
  x?: number;
  y?: number;
  [key: string]: unknown;
}

export interface GraphEdgeAttributes {
  [key: string]: unknown;
}

export type NoteGraph = Graph<GraphNodeAttributes, GraphEdgeAttributes>;

export function nodeLabel(node: AnyGraphNode): string {
  switch (node.kind) {
    case 'note':
      return node.title;
    case 'tag':
      return node.name;
    case 'dangling':
      return node.targetTitle;
  }
}

export function nodeDetail(node: AnyGraphNode): string {
  switch (node.kind) {
    case 'note':
      return node.folder || '/';
    case 'tag':
      return node.path;
    case 'dangling':
      return node.targetFolder || '/';
  }
}

function danglingNodeId(dangling: DanglingLinkItem): string {
  return `dangling:${dangling.target_folder}/${dangling.target_title}`;
}

/** Builds the graphology graph sigma renders from a fetched GraphResponse. Pure and
 * server-agnostic — callers fetch via the generated API client and pass the response in. */
export function buildGraph(response: GraphResponse): NoteGraph {
  const graph: NoteGraph = new Graph({ type: 'directed' });

  for (const node of response.nodes) {
    graph.mergeNode(node.id, { source: node });
  }

  for (const edge of response.edges) {
    if (graph.hasNode(edge.source) && graph.hasNode(edge.target)) {
      graph.mergeEdge(edge.source, edge.target);
    }
  }

  // Dangling links have no node of their own upstream — collapse every note that fails to
  // resolve to the same (folder, title) into a single synthetic node instead of one per edge.
  for (const dangling of response.dangling_links ?? []) {
    const id = danglingNodeId(dangling);
    if (!graph.hasNode(id)) {
      graph.addNode(id, {
        source: {
          kind: 'dangling',
          id,
          targetFolder: dangling.target_folder,
          targetTitle: dangling.target_title,
        },
      });
    }
    if (graph.hasNode(dangling.source_note_id)) {
      graph.mergeEdge(dangling.source_note_id, id);
    }
  }

  return graph;
}

/** graphology nodes have no position by default; ForceAtlas2 needs a starting layout. */
export function randomizePositions(graph: NoteGraph, spread = 100): void {
  graph.forEachNode((node) => {
    graph.mergeNodeAttributes(node, {
      x: Math.random() * spread,
      y: Math.random() * spread,
    });
  });
}
