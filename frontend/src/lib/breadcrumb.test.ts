import { describe, expect, it } from 'vitest';
import { breadcrumbCrumbs, parentFolder } from './breadcrumb';

describe('breadcrumbCrumbs', () => {
  it('returns empty for the root', () => {
    expect(breadcrumbCrumbs('')).toEqual([]);
  });
  it('returns one crumb for a top-level folder', () => {
    expect(breadcrumbCrumbs('a')).toEqual([{ label: 'a', folder: 'a' }]);
  });
  it('accumulates folder paths segment by segment', () => {
    expect(breadcrumbCrumbs('a/b/c')).toEqual([
      { label: 'a', folder: 'a' },
      { label: 'b', folder: 'a/b' },
      { label: 'c', folder: 'a/b/c' },
    ]);
  });
});

describe('parentFolder', () => {
  it('drops the last segment', () => {
    expect(parentFolder('a/b/c')).toBe('a/b');
  });
  it('returns root for a top-level folder', () => {
    expect(parentFolder('a')).toBe('');
  });
  it('returns root for the root', () => {
    expect(parentFolder('')).toBe('');
  });
});
