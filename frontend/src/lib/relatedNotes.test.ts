import { describe, expect, it } from 'vitest';
import { linkItem } from '../test/relatedFixtures';
import { headingLabel, linkRelations } from './relatedNotes';

describe('linkRelations', () => {
  it('marks backlinks, outlinks and mutual links', () => {
    const relations = linkRelations(
      'ws',
      [linkItem('a'), linkItem('c')],
      [linkItem('b'), linkItem('c')],
    );
    expect(relations.get('a')).toBe('backlink');
    expect(relations.get('b')).toBe('outlink');
    expect(relations.get('c')).toBe('both');
    expect(relations.has('d')).toBe(false);
  });

  it('counts links carrying the current workspace but ignores other workspaces', () => {
    const relations = linkRelations(
      'ws',
      [linkItem('a', 'ws'), linkItem('x', 'other')],
      [linkItem('y', 'other')],
    );
    expect([...relations.keys()]).toEqual(['a']);
  });
});

describe('headingLabel', () => {
  it('is null before the first heading', () => {
    expect(headingLabel([])).toBeNull();
    expect(headingLabel(['', '  '])).toBeNull();
  });

  it('joins a short path', () => {
    expect(headingLabel(['Plan', 'Ryzyka'])).toBe('Plan › Ryzyka');
  });

  it('keeps only the innermost levels of a deep path', () => {
    expect(headingLabel(['A', 'B', 'C', 'D'])).toBe('C › D');
  });
});
