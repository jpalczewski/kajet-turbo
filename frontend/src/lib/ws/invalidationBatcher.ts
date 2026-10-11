import { loadKeys } from '$lib/invalidation';
import type { ServerEvent } from './protocol';

export function eventKeys(event: ServerEvent): string[] {
  switch (event.type) {
    case 'note_updated':
      return [loadKeys.note(event.note_id), loadKeys.workspaceTree(event.workspace)];
    case 'workspace_changed':
      return [loadKeys.workspaceTree(event.workspace)];
  }
}

export type InvalidationBatcher = {
  push(keys: Iterable<string>): void;
  dispose(): void;
};

// Each WS message arrives as its own task, and SvelteKit coalesces only invalidate()
// calls made in the same tick -- so a burst of N events used to rerun the page loaders
// N times. Keys collect here until events stop for `delayMs` (or `maxWaitMs` passes, so
// a long stream still refreshes), then flush in one synchronous loop: no await between
// the calls, which lets SvelteKit fold the whole flush into a single rerun.
export function createInvalidationBatcher(
  invalidate: (key: string) => unknown,
  { delayMs = 150, maxWaitMs = 1000 } = {},
): InvalidationBatcher {
  const pending = new Set<string>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let firstPushAt: number | undefined;

  function flush(): void {
    clearTimeout(timer);
    timer = undefined;
    firstPushAt = undefined;
    const keys = [...pending];
    pending.clear();
    for (const key of keys) invalidate(key);
  }

  return {
    push(keys) {
      for (const key of keys) pending.add(key);
      const now = Date.now();
      firstPushAt ??= now;
      clearTimeout(timer);
      timer = setTimeout(flush, Math.max(0, Math.min(delayMs, firstPushAt + maxWaitMs - now)));
    },
    dispose() {
      clearTimeout(timer);
      timer = undefined;
      firstPushAt = undefined;
      pending.clear();
    },
  };
}
