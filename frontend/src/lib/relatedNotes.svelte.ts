import {
  apiNoteRelatedApiWorkspacesNameNotesNoteIdRelatedGet,
  type RelatedNoteItem,
  type RelatedNotesStatus,
} from '$lib/api';
import type { RelatedScope } from '$lib/relatedNotes';
import { wsConnection } from '$lib/ws/connection.svelte';

export const RELATED_LIMIT = 5;

// A `pending` note is waiting for its embeddings, which the backend finishes in the
// background without telling the client. Poll with growing gaps, then give up quietly.
export const PENDING_RETRY_MS = [5_000, 15_000, 30_000, 60_000];

const CACHE_TTL_MS = 60_000;
const CACHE_MAX_ENTRIES = 30;

type Ready = { status: RelatedNotesStatus; items: RelatedNoteItem[] };

// Module-level so that revisiting a note (or the same note in the other view) is instant.
// Only settled `ready` answers are kept; a short TTL bounds staleness from edits to *other*
// notes, and a `note_updated` event for the note itself evicts its entries immediately.
// Plain Map on purpose: nothing renders from the cache directly, the controller copies out.
// eslint-disable-next-line svelte/prefer-svelte-reactivity
const cache = new Map<string, { at: number; value: Ready }>();

const cacheKey = (slug: string, noteId: string, folder: string | null) =>
  `${slug}\0${noteId}\0${folder ?? ''}`;

function cacheGet(key: string): Ready | null {
  const hit = cache.get(key);
  if (!hit) return null;
  if (Date.now() - hit.at > CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return hit.value;
}

function cachePut(key: string, value: Ready): void {
  cache.delete(key);
  cache.set(key, { at: Date.now(), value });
  if (cache.size > CACHE_MAX_ENTRIES) cache.delete(cache.keys().next().value as string);
}

function cacheEvictNote(slug: string, noteId: string): void {
  const prefix = `${slug}\0${noteId}\0`;
  for (const key of cache.keys()) if (key.startsWith(prefix)) cache.delete(key);
}

export function clearRelatedCache(): void {
  cache.clear();
}

export type RelatedInput = { slug: string; noteId: string; folder: string };

/**
 * Owns the related-notes request for the note currently on screen.
 *
 * Must be constructed during component initialisation (it registers effects). The request
 * follows `input()`: any change of note, workspace or effective scope cancels the request in
 * flight and starts a fresh one, so a late answer for a previous note can never be shown.
 */
export class RelatedNotesController {
  scope = $state<RelatedScope>('workspace');
  phase = $state<'loading' | 'error' | 'ready'>('loading');
  status = $state<RelatedNotesStatus | null>(null);
  items = $state<RelatedNoteItem[]>([]);

  // Bumped to re-run the load effect for the same note (retry, poll, live update).
  #refresh = $state(0);
  // Refreshes that keep showing the current results instead of flashing the loading state.
  #quiet = false;
  #attempts = 0;
  #loadedKey = '';

  readonly #input: () => RelatedInput;

  constructor(input: () => RelatedInput) {
    this.#input = input;
    $effect(() => this.#load());
    $effect(() => this.#watchUpdates());
  }

  /** The workspace root has no narrower scope, so the folder option would be a no-op there. */
  get canScopeToFolder(): boolean {
    return this.#input().folder !== '';
  }

  get effectiveScope(): RelatedScope {
    return this.canScopeToFolder ? this.scope : 'workspace';
  }

  setScope(scope: RelatedScope): void {
    this.scope = scope;
  }

  retry(): void {
    this.#attempts = 0;
    this.#quiet = false;
    this.#refresh += 1;
  }

  #load(): (() => void) | void {
    const { slug, noteId, folder } = this.#input();
    const scopeFolder = this.effectiveScope === 'folder' ? folder : null;
    void this.#refresh;

    const key = cacheKey(slug, noteId, scopeFolder);
    if (key !== this.#loadedKey) {
      this.#loadedKey = key;
      this.#attempts = 0;
      this.#quiet = false;
    }

    const cached = cacheGet(key);
    if (cached) {
      this.#settle(cached);
      return;
    }

    if (!this.#quiet) {
      this.phase = 'loading';
      this.items = [];
      this.status = null;
    }
    this.#quiet = false;

    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    apiNoteRelatedApiWorkspacesNameNotesNoteIdRelatedGet(
      slug,
      noteId,
      { limit: RELATED_LIMIT, ...(scopeFolder === null ? {} : { folder: scopeFolder }) },
      { signal: controller.signal },
    )
      .then((result) => {
        // Belt and braces: a response that resolves in the same tick as the cleanup must
        // still be dropped even though the abort raced it.
        if (controller.signal.aborted) return;
        if (result.status !== 200) {
          this.phase = 'error';
          return;
        }
        const value = { status: result.data.status, items: result.data.items };
        if (value.status === 'ready') cachePut(key, value);
        this.#settle(value);
        timer = this.#schedulePendingPoll(value.status);
      })
      .catch(() => {
        if (!controller.signal.aborted) this.phase = 'error';
      });

    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }

  #settle(value: Ready): void {
    this.status = value.status;
    this.items = value.items;
    this.phase = 'ready';
  }

  #schedulePendingPoll(status: RelatedNotesStatus): ReturnType<typeof setTimeout> | undefined {
    if (status !== 'pending' || this.#attempts >= PENDING_RETRY_MS.length) return undefined;
    const delay = PENDING_RETRY_MS[this.#attempts++];
    return setTimeout(() => {
      this.#quiet = true;
      this.#refresh += 1;
    }, delay);
  }

  #watchUpdates(): () => void {
    return wsConnection.onEvent((event) => {
      const { slug, noteId } = this.#input();
      if (event.type !== 'note_updated' || event.workspace !== slug || event.note_id !== noteId) {
        return;
      }
      cacheEvictNote(slug, noteId);
      this.#attempts = 0;
      this.#quiet = true;
      this.#refresh += 1;
    });
  }
}
