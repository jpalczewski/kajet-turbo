import type { NodeDisplayData, EdgeDisplayData } from 'sigma/types';
import {
  nodeLabel,
  type GraphEdgeAttributes,
  type GraphNodeAttributes,
  type NodeKind,
} from './model';

// No theming abstraction exists in the frontend yet (dark-only app, see
// frontend/src/lib/styles/_variables.scss) — these mirror that palette directly rather than
// reading CSS custom properties that don't exist.
const NODE_COLOR: Record<NodeKind, string> = {
  note: '#ece8f5', // $text-primary
  tag: '#9d5cff', // $violet
  dangling: '#ff4d6b', // $error
};

const NODE_SIZE: Record<NodeKind, number> = {
  note: 4,
  tag: 6, // hub role — visually larger than the notes it connects
  dangling: 4,
};

export const EDGE_COLOR = '#6a4fb0'; // $border-accent

export function nodeReducer(_key: string, attrs: GraphNodeAttributes): Partial<NodeDisplayData> {
  const kind = attrs.source.kind;
  return {
    // Sigma replaces a node's whole display record with whatever the reducer returns, rather
    // than merging into it — x/y have no sensible default, so they must be passed through
    // explicitly or sigma throws "could not find a valid position" for every node.
    x: attrs.x,
    y: attrs.y,
    label: nodeLabel(attrs.source),
    color: NODE_COLOR[kind],
    size: NODE_SIZE[kind],
  };
}

export function edgeReducer(_key: string, _attrs: GraphEdgeAttributes): Partial<EdgeDisplayData> {
  return { color: EDGE_COLOR };
}
