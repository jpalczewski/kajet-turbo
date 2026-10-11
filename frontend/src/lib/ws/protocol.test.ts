import { describe, expect, it } from 'vitest';
import { parseServerEvent } from './protocol';

describe('parseServerEvent', () => {
  it('returns a known event', () => {
    const raw = JSON.stringify({ type: 'workspace_changed', owner_id: 'u1', workspace: 'ws-1' });
    expect(parseServerEvent(raw)).toEqual({
      type: 'workspace_changed',
      owner_id: 'u1',
      workspace: 'ws-1',
    });
  });

  it.each([
    ['malformed JSON', '{not json'],
    ['a non-object', '"note_updated"'],
    ['null', 'null'],
    ['a missing type', '{"workspace":"ws-1"}'],
    ['an unknown type', '{"type":"notices_changed","owner_id":"u1"}'],
  ])('drops %s', (_, raw) => {
    expect(parseServerEvent(raw)).toBeNull();
  });
});
