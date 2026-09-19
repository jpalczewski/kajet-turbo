import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { RelatedNotesResponse } from '$lib/api';
import { clearRelatedCache } from '$lib/relatedNotes.svelte';
import { linkItem, relatedItem } from '../../../test/relatedFixtures';
import NoteMeta from './NoteMeta.svelte';

const mocks = vi.hoisted(() => ({ related: vi.fn(), neighborhood: vi.fn() }));

vi.mock('$lib/api', () => ({
  apiNoteRelatedApiWorkspacesNameNotesNoteIdRelatedGet: mocks.related,
  apiNoteNeighborhoodApiWorkspacesNameNotesNoteIdNeighborhoodGet: mocks.neighborhood,
}));
vi.mock('$lib/ws/connection.svelte', () => ({ wsConnection: { onEvent: () => () => {} } }));
// Sigma needs WebGL; the graph view is irrelevant to the related-notes section.
vi.mock('$lib/components/GraphView.svelte', async () => ({
  default: (await import('../../../test/EmptyComponent.svelte')).default,
}));

const reply = (...titles: string[]) =>
  Promise.resolve({
    status: 200,
    data: {
      status: 'ready',
      items: titles.map((title, i) => relatedItem({ note_id: `${title}-${i}`, title })),
    } satisfies RelatedNotesResponse,
  });

// The relations lists above also render note links, so scope queries to the related section.
const section = () => within(screen.getByRole('region', { name: 'Powiązane' }));

const props = (over: Record<string, unknown> = {}) => ({
  slug: 'ws',
  noteId: 'note-a',
  folder: 'proj',
  tags: [],
  outline: [],
  backlinks: [],
  outlinks: [linkItem('Alpha-0')],
  ...over,
});

beforeEach(() => {
  mocks.related.mockReset();
  mocks.neighborhood.mockReset();
  mocks.neighborhood.mockResolvedValue({ status: 200, data: { nodes: [], edges: [] } });
  clearRelatedCache();
  localStorage.clear();
});

describe('NoteMeta related notes', () => {
  it('shows the shared section with link marking', async () => {
    mocks.related.mockReturnValue(reply('Alpha', 'Beta'));
    render(NoteMeta, props());
    await screen.findByRole('region', { name: 'Powiązane' });
    const alpha = await section().findByRole('link', { name: /Alpha/ });
    expect(alpha).toHaveTextContent('Bieżąca notatka linkuje do tej');
    expect(section().getByRole('link', { name: /Beta/ })).not.toHaveTextContent('linkuje');
  });

  it('starts loading even when the whole rail is collapsed', async () => {
    localStorage.setItem('kajet:note-meta-collapsed', '1');
    mocks.related.mockReturnValue(reply('Alpha'));
    render(NoteMeta, props());
    expect(mocks.related).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('region', { name: 'Powiązane' })).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: 'Pokaż panel' }));
    expect(await section().findByRole('link', { name: /Alpha/ })).toBeInTheDocument();
    expect(mocks.related).toHaveBeenCalledTimes(1);
  });

  it('reloads for a new note when the preview keeps the same instance', async () => {
    mocks.related.mockReturnValueOnce(reply('FromA')).mockReturnValueOnce(reply('FromB'));
    const { rerender } = render(NoteMeta, props());
    await section().findByRole('link', { name: /FromA/ });

    await rerender(props({ noteId: 'note-b', folder: '' }));
    expect(await section().findByRole('link', { name: /FromB/ })).toBeInTheDocument();
    expect(screen.queryByText('FromA')).toBeNull();
    expect(mocks.related.mock.calls[1][1]).toBe('note-b');
    expect(screen.queryByRole('group', { name: /Zakres/ })).toBeNull();
  });

  it('keeps loading while the graph view is open but does not render the section', async () => {
    mocks.related.mockReturnValue(reply('Alpha'));
    render(NoteMeta, props());
    await section().findByRole('link', { name: /Alpha/ });
    await userEvent.click(screen.getByRole('button', { name: 'Graf' }));
    expect(screen.queryByRole('region', { name: 'Powiązane' })).toBeNull();
    expect(mocks.related).toHaveBeenCalledTimes(1);
  });
});
