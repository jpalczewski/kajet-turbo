import { render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RelatedNotesResponse } from '$lib/api';
import RelatedHarness from '../test/RelatedHarness.svelte';
import { deferred, linkItem, relatedItem, type Deferred } from '../test/relatedFixtures';
import { clearRelatedCache, PENDING_RETRY_MS } from './relatedNotes.svelte';

type Reply = { status: 200; data: RelatedNotesResponse };
type Call = { noteId: string; params: Record<string, unknown>; signal?: AbortSignal };

const mocks = vi.hoisted(() => ({
  api: vi.fn(),
  handlers: [] as Array<(event: unknown) => void>,
}));

vi.mock('$lib/api', () => ({
  apiNoteRelatedApiWorkspacesNameNotesNoteIdRelatedGet: mocks.api,
}));
vi.mock('$lib/ws/connection.svelte', () => ({
  wsConnection: {
    onEvent: (handler: (event: unknown) => void) => {
      mocks.handlers.push(handler);
      return () => mocks.handlers.splice(mocks.handlers.indexOf(handler), 1);
    },
  },
}));

const ready = (...titles: string[]): Reply => ({
  status: 200,
  data: {
    status: 'ready',
    items: titles.map((title, i) => relatedItem({ note_id: `${title}-${i}`, title })),
  },
});
const withStatus = (status: 'pending' | 'unavailable' | 'empty'): Reply => ({
  status: 200,
  data: { status, items: [] },
});

const calls = (): Call[] =>
  mocks.api.mock.calls.map(([, noteId, params, options]) => ({
    noteId,
    params,
    signal: options?.signal,
  }));

// Queue one deferred response per expected request, in call order.
function queue(count: number): Array<Deferred<Reply>> {
  const pending = Array.from({ length: count }, () => deferred<Reply>());
  pending.forEach((d) => mocks.api.mockReturnValueOnce(d.promise));
  return pending;
}

// Let every already-settled promise chain (then -> catch) run before asserting an absence.
const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

const props = (over: Record<string, unknown> = {}) => ({
  slug: 'ws',
  noteId: 'note-a',
  folder: 'proj/x',
  ...over,
});

beforeEach(() => {
  mocks.api.mockReset();
  mocks.handlers.length = 0;
  clearRelatedCache();
});
afterEach(() => vi.useRealTimers());

describe('request shape', () => {
  it('starts on mount with limit 5 and no folder (workspace scope)', async () => {
    const [d] = queue(1);
    render(RelatedHarness, props());
    expect(screen.getByRole('status')).toHaveTextContent('Szukam');
    expect(calls()).toHaveLength(1);
    expect(calls()[0]).toMatchObject({ noteId: 'note-a', params: { limit: 5 } });
    expect(calls()[0].params).not.toHaveProperty('folder');
    d.resolve(ready('Alpha'));
    expect(await screen.findByRole('link', { name: /Alpha/ })).toBeInTheDocument();
  });

  it('passes the note folder when the folder scope is chosen', async () => {
    const [first, second] = queue(2);
    render(RelatedHarness, props());
    first.resolve(ready('Alpha'));
    await screen.findByRole('link', { name: /Alpha/ });

    await userEvent.click(screen.getByRole('button', { name: 'Folder' }));
    expect(calls()[1].params).toEqual({ limit: 5, folder: 'proj/x' });
    expect(screen.getByRole('button', { name: 'Folder' })).toHaveAttribute('aria-pressed', 'true');
    second.resolve(ready('Beta'));
    expect(await screen.findByRole('link', { name: /Beta/ })).toBeInTheDocument();
  });

  it('hides the toggle and never sends a folder for a root note', async () => {
    const [d] = queue(1);
    render(RelatedHarness, props({ folder: '' }));
    d.resolve(ready('Alpha'));
    await screen.findByRole('link', { name: /Alpha/ });
    expect(screen.queryByRole('group', { name: /Zakres/ })).toBeNull();
    expect(calls()[0].params).not.toHaveProperty('folder');
  });

  it('falls back to the workspace scope when a folder-scoped user opens a root note', async () => {
    const [a, b] = queue(2);
    const { rerender } = render(RelatedHarness, props());
    a.resolve(ready('Alpha'));
    await screen.findByRole('link', { name: /Alpha/ });
    await userEvent.click(screen.getByRole('button', { name: 'Folder' }));
    b.resolve(ready('Beta'));
    await screen.findByRole('link', { name: /Beta/ });

    const [c] = queue(1);
    await rerender(props({ noteId: 'note-root', folder: '' }));
    expect(calls()[2]).toMatchObject({ noteId: 'note-root' });
    expect(calls()[2].params).not.toHaveProperty('folder');
    c.resolve(ready('Gamma'));
    await screen.findByRole('link', { name: /Gamma/ });
  });
});

describe('stale responses', () => {
  it('ignores a late response for the previous note and aborts its request', async () => {
    const [forA, forB] = queue(2);
    const { rerender } = render(RelatedHarness, props());
    const first = calls()[0];

    await rerender(props({ noteId: 'note-b' }));
    expect(first.signal?.aborted).toBe(true);
    expect(screen.getByRole('status')).toHaveTextContent('Szukam');

    forB.resolve(ready('FromB'));
    expect(await screen.findByRole('link', { name: /FromB/ })).toBeInTheDocument();

    forA.resolve(ready('FromA'));
    await flush();
    expect(screen.queryByText('FromA')).toBeNull();
    expect(screen.getByRole('link', { name: /FromB/ })).toBeInTheDocument();
  });

  it('ignores a late response after a scope switch on the same note', async () => {
    const [workspace, folder] = queue(2);
    render(RelatedHarness, props());
    await userEvent.click(screen.getByRole('button', { name: 'Folder' }));

    folder.resolve(ready('InFolder'));
    await screen.findByRole('link', { name: /InFolder/ });
    workspace.resolve(ready('WholeWorkspace'));
    await flush();
    expect(screen.queryByText('WholeWorkspace')).toBeNull();
  });

  it('does not let a late error for the previous note replace current results', async () => {
    const [forA, forB] = queue(2);
    const { rerender } = render(RelatedHarness, props());
    await rerender(props({ noteId: 'note-b' }));
    forB.resolve(ready('FromB'));
    await screen.findByRole('link', { name: /FromB/ });
    forA.reject(new Error('boom'));
    await flush();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

describe('states from the response', () => {
  it.each([
    ['pending', 'indeksowana'],
    ['unavailable', 'nie jest skonfigurowane'],
    ['empty', 'nie ma jeszcze treści'],
  ] as const)('renders %s', async (status, text) => {
    const [d] = queue(1);
    vi.useFakeTimers();
    render(RelatedHarness, props());
    d.resolve(withStatus(status));
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByRole('status')).toHaveTextContent(text);
  });

  it('renders a request error and recovers on retry', async () => {
    const [failing, retried] = queue(2);
    render(RelatedHarness, props());
    failing.reject(Object.assign(new Error('HTTP 500'), { status: 500 }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Spróbuj ponownie' }));
    expect(screen.getByRole('status')).toHaveTextContent('Szukam');
    retried.resolve(ready('Alpha'));
    expect(await screen.findByRole('link', { name: /Alpha/ })).toBeInTheDocument();
  });
});

describe('link marking', () => {
  it('flags a result that is already a backlink without dropping it', async () => {
    const [d] = queue(1);
    render(RelatedHarness, props({ backlinks: [linkItem('Alpha-0')] }));
    d.resolve(ready('Alpha', 'Beta'));
    const alpha = await screen.findByRole('link', { name: /Alpha/ });
    expect(alpha).toHaveTextContent('Ta notatka linkuje do bieżącej');
    expect(screen.getByRole('link', { name: /Beta/ })).not.toHaveTextContent('linkuje');
  });
});

describe('cache and refresh', () => {
  it('serves a revisited note from cache without a new request', async () => {
    const [a, b] = queue(2);
    const { rerender } = render(RelatedHarness, props());
    a.resolve(ready('FromA'));
    await screen.findByRole('link', { name: /FromA/ });
    await rerender(props({ noteId: 'note-b' }));
    b.resolve(ready('FromB'));
    await screen.findByRole('link', { name: /FromB/ });

    await rerender(props());
    expect(await screen.findByRole('link', { name: /FromA/ })).toBeInTheDocument();
    expect(mocks.api).toHaveBeenCalledTimes(2);
  });

  it('expires cached results after the TTL', async () => {
    vi.useFakeTimers();
    const [a, b, c] = queue(3);
    const { rerender } = render(RelatedHarness, props());
    a.resolve(ready('FromA'));
    await vi.advanceTimersByTimeAsync(0);
    await rerender(props({ noteId: 'note-b' }));
    b.resolve(ready('FromB'));
    await vi.advanceTimersByTimeAsync(61_000);

    await rerender(props());
    expect(mocks.api).toHaveBeenCalledTimes(3);
    c.resolve(ready('Fresh'));
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByRole('link', { name: /Fresh/ })).toBeInTheDocument();
  });

  it('refetches quietly when the note itself is updated', async () => {
    const [a, b] = queue(2);
    render(RelatedHarness, props());
    a.resolve(ready('Old'));
    await screen.findByRole('link', { name: /Old/ });

    mocks.handlers.forEach((h) =>
      h({
        type: 'note_updated',
        workspace: 'ws',
        note_id: 'note-a',
        owner_id: 'u',
        updated_at: '',
      }),
    );
    await waitFor(() => expect(mocks.api).toHaveBeenCalledTimes(2));
    // Old results stay visible until the fresh ones land.
    expect(screen.getByRole('link', { name: /Old/ })).toBeInTheDocument();
    b.resolve(ready('New'));
    expect(await screen.findByRole('link', { name: /New/ })).toBeInTheDocument();
  });

  it('ignores updates of other notes', async () => {
    const [a] = queue(1);
    render(RelatedHarness, props());
    a.resolve(ready('Old'));
    await screen.findByRole('link', { name: /Old/ });
    mocks.handlers.forEach((h) =>
      h({ type: 'note_updated', workspace: 'ws', note_id: 'other', owner_id: 'u', updated_at: '' }),
    );
    await flush();
    expect(mocks.api).toHaveBeenCalledTimes(1);
  });
});

describe('pending polling', () => {
  it('re-asks with growing gaps while pending, then stops', async () => {
    vi.useFakeTimers();
    mocks.api.mockResolvedValue(withStatus('pending'));
    render(RelatedHarness, props());
    await vi.advanceTimersByTimeAsync(0);
    expect(mocks.api).toHaveBeenCalledTimes(1);

    for (const [i, delay] of PENDING_RETRY_MS.entries()) {
      await vi.advanceTimersByTimeAsync(delay - 1);
      expect(mocks.api).toHaveBeenCalledTimes(i + 1);
      await vi.advanceTimersByTimeAsync(1);
      expect(mocks.api).toHaveBeenCalledTimes(i + 2);
    }
    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(mocks.api).toHaveBeenCalledTimes(PENDING_RETRY_MS.length + 1);
    // Still showing the pending message: giving up is silent.
    expect(screen.getByRole('status')).toHaveTextContent('indeksowana');
  });

  it('picks up results once indexing finishes, without a loading flash', async () => {
    vi.useFakeTimers();
    mocks.api.mockResolvedValueOnce(withStatus('pending')).mockResolvedValueOnce(ready('Done'));
    render(RelatedHarness, props());
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(PENDING_RETRY_MS[0]);
    expect(screen.getByRole('link', { name: /Done/ })).toBeInTheDocument();
  });

  it('stops polling when the note changes', async () => {
    vi.useFakeTimers();
    mocks.api.mockResolvedValueOnce(withStatus('pending')).mockResolvedValue(ready('B'));
    const { rerender } = render(RelatedHarness, props());
    await vi.advanceTimersByTimeAsync(0);
    await rerender(props({ noteId: 'note-b' }));
    await vi.advanceTimersByTimeAsync(0);
    const before = mocks.api.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5 * 60_000);
    expect(mocks.api).toHaveBeenCalledTimes(before);
  });
});
