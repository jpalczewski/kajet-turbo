import { describe, expect, it } from 'vitest';
import { computeCandidates, computeOptions } from './tagEditor';

describe('computeCandidates', () => {
  it('normalizes, dedupes, and drops invalid suggestions', () => {
    expect(computeCandidates([], ['Work', 'work', 'dom', '', '##'])).toEqual(['work', 'dom']);
  });
  it('excludes suggestions already applied as tags', () => {
    expect(computeCandidates(['work'], ['Work', 'dom'])).toEqual(['dom']);
  });
});

describe('computeOptions', () => {
  it('returns nothing for an empty query', () => {
    expect(computeOptions('', ['work', 'dom'], [])).toEqual([]);
  });
  it('filters candidates by substring and offers a create option', () => {
    expect(computeOptions('wo', ['work', 'dom', 'workshop'], [])).toEqual([
      { value: 'work', isCreate: false },
      { value: 'workshop', isCreate: false },
      { value: 'wo', isCreate: true },
    ]);
  });
  it('suppresses the create option on an exact candidate match', () => {
    expect(computeOptions('work', ['work', 'workshop'], [])).toEqual([
      { value: 'work', isCreate: false },
      { value: 'workshop', isCreate: false },
    ]);
  });
  it('suppresses the create option when the tag is already applied', () => {
    expect(computeOptions('dom', ['x'], ['dom'])).toEqual([]);
  });
  it('caps matches at 8', () => {
    const candidates = ['aa', 'ab', 'ac', 'ad', 'ae', 'af', 'ag', 'ah', 'ai'];
    const options = computeOptions('a', candidates, []);
    expect(options.filter((o) => !o.isCreate)).toHaveLength(8);
  });
});
