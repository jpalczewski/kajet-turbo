import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { NoteHtmlResponse } from '$lib/api';
import HistoryPage from './+page.svelte';

const mocks = vi.hoisted(() => ({
  version: vi.fn(),
  restore: vi.fn(),
  goto: vi.fn(),
  invalidate: vi.fn(),
}));

vi.mock('$lib/api', () => ({
  apiNoteVersionApiWorkspacesNameNotesNoteIdHistoryShaGet: mocks.version,
  apiRestoreNoteVersionApiWorkspacesNameNotesNoteIdHistoryShaRestorePost: mocks.restore,
}));
vi.mock('$app/navigation', () => ({ goto: mocks.goto, invalidate: mocks.invalidate }));
vi.mock('$app/state', () => ({
  page: {
    params: { slug: 'ws', id: 'note-a' },
    data: { entries: [{ sha: 'abc123', message: 'note: update Alpha', timestamp: 0 }] },
  },
}));

const apiFailure = (status: number) =>
  Promise.reject(Object.assign(new Error(`HTTP ${status}`), { status, data: {} }));

const version: NoteHtmlResponse = {
  note_id: 'note-a',
  title: 'Alpha',
  folder: '',
  tags: [],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  content_html: '<p>old body</p>',
  sha: 'abc123',
};

const selectVersion = () =>
  userEvent.click(screen.getByRole('button', { name: /note: update Alpha/ }));

beforeEach(() => {
  for (const mock of Object.values(mocks)) mock.mockReset();
});

describe('note history page', () => {
  it('shows an error when a version fails to load', async () => {
    mocks.version.mockImplementation(() => apiFailure(500));
    render(HistoryPage);
    await selectVersion();
    expect(await screen.findByText('HTTP 500')).toBeInTheDocument();
  });

  it('stays on the page with an error when restoring fails', async () => {
    mocks.version.mockResolvedValue({ status: 200, data: version });
    mocks.restore.mockImplementation(() => apiFailure(500));
    render(HistoryPage);
    await selectVersion();
    await userEvent.click(await screen.findByRole('button', { name: 'Przywróć tę wersję' }));
    expect(await screen.findByText('HTTP 500')).toBeInTheDocument();
    expect(mocks.goto).not.toHaveBeenCalled();
  });
});
