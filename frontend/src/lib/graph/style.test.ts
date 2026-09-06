import { describe, expect, it } from 'vitest';
import type { GraphNodeAttributes } from './model';
import { edgeReducer, nodeReducer } from './style';

function attrs(overrides: GraphNodeAttributes['source']): GraphNodeAttributes {
  return { source: overrides };
}

describe('nodeReducer', () => {
  it('gives each node kind a distinct color', () => {
    const note = nodeReducer(
      'n1',
      attrs({ id: 'n1', note_id: 'n1', title: 'A', folder: '', kind: 'note' }),
    );
    const tag = nodeReducer(
      't1',
      attrs({ id: 't1', kind: 'tag', path: 'x', name: 'x', workspace: 'ws' }),
    );
    const dangling = nodeReducer(
      'd1',
      attrs({ kind: 'dangling', id: 'd1', targetFolder: '', targetTitle: 'x' }),
    );

    const colors = new Set([note.color, tag.color, dangling.color]);
    expect(colors.size).toBe(3);
  });

  it('labels a note by its title', () => {
    const result = nodeReducer(
      'n1',
      attrs({ id: 'n1', note_id: 'n1', title: 'Plan Q3', folder: '', kind: 'note' }),
    );
    expect(result.label).toBe('Plan Q3');
  });
});

describe('edgeReducer', () => {
  it('returns a fixed edge color', () => {
    const result = edgeReducer('e1', {});
    expect(result.color).toBeTruthy();
  });
});
