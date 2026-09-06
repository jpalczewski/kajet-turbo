import { describe, expect, it } from 'vitest';
import type { GraphResponse } from '$lib/api';
import { buildGraph, nodeDetail, nodeLabel, randomizePositions } from './model';

function response(overrides: Partial<GraphResponse> = {}): GraphResponse {
  return {
    nodes: [],
    edges: [],
    ...overrides,
  };
}

describe('buildGraph', () => {
  it('adds note and tag nodes with edges between them', () => {
    const graph = buildGraph(
      response({
        nodes: [
          { id: 'n1', note_id: 'n1', title: 'Plan Q3', folder: 'praca', kind: 'note' },
          { id: 'n2', note_id: 'n2', title: 'Notatki', folder: '', kind: 'note' },
          { id: 't1', kind: 'tag', path: 'praca/projekty', name: 'projekty', workspace: 'ws' },
        ],
        edges: [
          { source: 'n1', target: 'n2' },
          { source: 'n1', target: 't1' },
        ],
      }),
    );

    expect(graph.order).toBe(3);
    expect(graph.size).toBe(2);
    expect(graph.hasEdge('n1', 'n2')).toBe(true);
    expect(graph.hasEdge('n1', 't1')).toBe(true);
  });

  it('drops edges referencing an unknown node instead of throwing', () => {
    const graph = buildGraph(
      response({
        nodes: [{ id: 'n1', note_id: 'n1', title: 'Solo', folder: '', kind: 'note' }],
        edges: [{ source: 'n1', target: 'ghost' }],
      }),
    );

    expect(graph.order).toBe(1);
    expect(graph.size).toBe(0);
  });

  it('collapses dangling links to the same missing title into one synthetic node', () => {
    const graph = buildGraph(
      response({
        nodes: [
          { id: 'n1', note_id: 'n1', title: 'A', folder: '', kind: 'note' },
          { id: 'n2', note_id: 'n2', title: 'B', folder: '', kind: 'note' },
        ],
        dangling_links: [
          { source_note_id: 'n1', target_folder: 'praca', target_title: 'Brakująca' },
          { source_note_id: 'n2', target_folder: 'praca', target_title: 'Brakująca' },
        ],
      }),
    );

    expect(graph.order).toBe(3);
    const danglingId = 'dangling:praca/Brakująca';
    expect(graph.hasNode(danglingId)).toBe(true);
    expect(graph.hasEdge('n1', danglingId)).toBe(true);
    expect(graph.hasEdge('n2', danglingId)).toBe(true);
  });

  it('treats a null dangling_links (validation on) the same as an empty list', () => {
    const withNull = buildGraph(
      response({
        nodes: [{ id: 'n1', note_id: 'n1', title: 'A', folder: '', kind: 'note' }],
        dangling_links: null,
      }),
    );
    const withEmpty = buildGraph(
      response({
        nodes: [{ id: 'n1', note_id: 'n1', title: 'A', folder: '', kind: 'note' }],
        dangling_links: [],
      }),
    );

    expect(withNull.order).toBe(1);
    expect(withEmpty.order).toBe(1);
  });
});

describe('randomizePositions', () => {
  it('assigns numeric x/y to every node', () => {
    const graph = buildGraph(
      response({
        nodes: [
          { id: 'n1', note_id: 'n1', title: 'A', folder: '', kind: 'note' },
          { id: 'n2', note_id: 'n2', title: 'B', folder: '', kind: 'note' },
        ],
      }),
    );

    randomizePositions(graph);

    graph.forEachNode((_node, attrs) => {
      expect(typeof attrs.x).toBe('number');
      expect(typeof attrs.y).toBe('number');
    });
  });
});

describe('nodeLabel / nodeDetail', () => {
  it('reads note fields', () => {
    const node = { id: 'n1', note_id: 'n1', title: 'Plan', folder: 'praca', kind: 'note' } as const;
    expect(nodeLabel(node)).toBe('Plan');
    expect(nodeDetail(node)).toBe('praca');
  });

  it('falls back to / for a root-folder note', () => {
    const node = { id: 'n1', note_id: 'n1', title: 'Plan', folder: '', kind: 'note' } as const;
    expect(nodeDetail(node)).toBe('/');
  });

  it('reads tag fields', () => {
    const node = {
      id: 't1',
      kind: 'tag',
      path: 'praca/projekty',
      name: 'projekty',
      workspace: 'ws',
    } as const;
    expect(nodeLabel(node)).toBe('projekty');
    expect(nodeDetail(node)).toBe('praca/projekty');
  });

  it('reads dangling fields', () => {
    const node = {
      kind: 'dangling',
      id: 'dangling:x',
      targetFolder: 'praca',
      targetTitle: 'Brak',
    } as const;
    expect(nodeLabel(node)).toBe('Brak');
    expect(nodeDetail(node)).toBe('praca');
  });
});
