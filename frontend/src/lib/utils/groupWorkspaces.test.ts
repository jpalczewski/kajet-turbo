import { describe, expect, it } from 'vitest';
import { groupWorkspaces } from './groupWorkspaces';

const ws = (name: string, folder = '') => ({
  name,
  folder,
  description: '',
  tags: [],
  file_count: 0,
  last_commit_at: null,
});

describe('groupWorkspaces', () => {
  it('puts root (empty folder) first, then folders alphabetically', () => {
    const groups = groupWorkspaces([ws('b', 'Praca'), ws('a'), ws('c', 'Archiwum')]);
    expect(groups.map((g) => g.folder)).toEqual(['', 'Archiwum', 'Praca']);
  });

  it('sorts items by name within a group', () => {
    const groups = groupWorkspaces([ws('z', 'P'), ws('a', 'P')]);
    expect(groups[0].items.map((i) => i.name)).toEqual(['a', 'z']);
  });
});
