import { describe, expect, it } from 'vitest';
import { activePane } from './explorerView';

describe('activePane', () => {
  it('shows the preview when a note is explicitly selected', () => {
    expect(activePane({ noteSelected: true })).toBe('preview');
  });
  it('shows the list (folder view) when no note is selected', () => {
    expect(activePane({ noteSelected: false })).toBe('list');
  });
});
