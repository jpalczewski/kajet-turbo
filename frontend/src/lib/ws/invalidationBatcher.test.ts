import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInvalidationBatcher, eventKeys } from './invalidationBatcher';
import type { ServerEvent } from './protocol';

const noteUpdated = (noteId: string, workspace = 'ws-1'): ServerEvent => ({
  type: 'note_updated',
  owner_id: 'u1',
  workspace,
  note_id: noteId,
  updated_at: '2026-01-01T00:00:00+00:00',
});

describe('eventKeys', () => {
  it('maps note_updated to the note and its workspace tree', () => {
    expect(eventKeys(noteUpdated('n1'))).toEqual(['app:note:n1', 'app:workspace-tree:ws-1']);
  });

  it('maps workspace_changed to its workspace tree only', () => {
    const event: ServerEvent = { type: 'workspace_changed', owner_id: 'u1', workspace: 'ws-2' };
    expect(eventKeys(event)).toEqual(['app:workspace-tree:ws-2']);
  });
});

describe('createInvalidationBatcher', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('collapses a burst into one invalidate per distinct key', () => {
    const invalidate = vi.fn();
    const batcher = createInvalidationBatcher(invalidate, { delayMs: 150 });

    for (let i = 0; i < 40; i++) {
      batcher.push(eventKeys(noteUpdated(`n${i % 3}`)));
      vi.advanceTimersByTime(20);
    }
    expect(invalidate).not.toHaveBeenCalled();

    vi.advanceTimersByTime(150);
    expect(invalidate.mock.calls.map(([key]) => key).sort()).toEqual([
      'app:note:n0',
      'app:note:n1',
      'app:note:n2',
      'app:workspace-tree:ws-1',
    ]);
  });

  it('flushes a stream that never pauses once maxWaitMs has passed', () => {
    const invalidate = vi.fn();
    const batcher = createInvalidationBatcher(invalidate, { delayMs: 150, maxWaitMs: 1000 });

    for (let elapsed = 0; elapsed < 1000; elapsed += 100) {
      batcher.push(['app:workspace-tree:ws-1']);
      vi.advanceTimersByTime(100);
    }
    expect(invalidate).toHaveBeenCalledTimes(1);

    batcher.push(['app:workspace-tree:ws-1']);
    vi.advanceTimersByTime(149);
    expect(invalidate).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    expect(invalidate).toHaveBeenCalledTimes(2);
  });

  it('drops a pending flush on dispose', () => {
    const invalidate = vi.fn();
    const batcher = createInvalidationBatcher(invalidate);

    batcher.push(['app:workspace-tree:ws-1']);
    batcher.dispose();
    vi.runAllTimers();

    expect(invalidate).not.toHaveBeenCalled();
  });
});
