import { describe, expect, it } from 'vitest';
import { ancestors, buildTree, childFolders } from './tree';

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

describe('childFolders', () => {
  const all = ['a', 'b', 'a/x', 'a/y', 'a/x/z'];

  it('returns top-level folders for the root', () => {
    expect(childFolders(all, '')).toEqual(['a', 'b']);
  });
  it('returns immediate children of a folder', () => {
    expect(childFolders(all, 'a')).toEqual(['a/x', 'a/y']);
  });
  it('returns deeper immediate children', () => {
    expect(childFolders(all, 'a/x')).toEqual(['a/x/z']);
  });
  it('returns empty when there are no children', () => {
    expect(childFolders(all, 'b')).toEqual([]);
  });
});
