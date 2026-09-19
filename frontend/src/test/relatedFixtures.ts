import type { NoteLinkItem, RelatedNoteItem } from '$lib/api';

export const relatedItem = (overrides: Partial<RelatedNoteItem> = {}): RelatedNoteItem => ({
  note_id: 'n-1',
  title: 'Sample related',
  folder: '',
  updated_at: '2026-01-01T00:00:00Z',
  source_chunk_id: 's-1',
  target_chunk_id: 't-1',
  source_header_path: [],
  target_header_path: [],
  target_content: 'chunk text',
  best_distance: 0.4242,
  hub_margin: 0.1717,
  coverage: 0.3131,
  score: 0.8686,
  ...overrides,
});

export const linkItem = (note_id: string, workspace?: string): NoteLinkItem => ({
  note_id,
  title: `Link ${note_id}`,
  folder: '',
  ...(workspace ? { workspace } : {}),
});

export type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
};

export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
