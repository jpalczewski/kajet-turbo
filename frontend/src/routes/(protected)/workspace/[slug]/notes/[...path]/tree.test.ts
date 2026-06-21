import { describe, expect, it } from 'vitest';
import { ancestors, buildTree } from './tree';

describe('buildTree', () => {
  it('nests children under their parent folder', () => {
    const tree = buildTree(['a', 'b', 'a/x']);
    expect(tree.map((n) => n.fullPath)).toEqual(['a', 'b']);
    const a = tree.find((n) => n.fullPath === 'a')!;
    expect(a.children.map((n) => n.fullPath)).toEqual(['a/x']);
  });
});

describe('ancestors', () => {
  it('returns each ancestor path including the folder itself', () => {
    expect(ancestors('a/b/c')).toEqual(['a', 'a/b', 'a/b/c']);
  });
  it('returns empty for the root', () => {
    expect(ancestors('')).toEqual([]);
  });
});
