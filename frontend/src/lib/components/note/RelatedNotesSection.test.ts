import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { RelatedNotesStatus } from '$lib/api';
import type { LinkRelation } from '$lib/relatedNotes';
import { relatedItem } from '../../../test/relatedFixtures';
import RelatedNotesSection from './RelatedNotesSection.svelte';

type Props = Parameters<typeof render<typeof RelatedNotesSection>>[1];

const base = {
  slug: 'ws',
  phase: 'ready' as const,
  status: 'ready' as RelatedNotesStatus | null,
  items: [],
  scope: 'workspace' as const,
  canScopeToFolder: true,
  relations: new Map<string, LinkRelation>(),
  onscope: () => {},
  onretry: () => {},
};

const renderSection = (props: Partial<NonNullable<Props>> = {}) =>
  render(RelatedNotesSection, { ...base, ...props } as never);

describe('RelatedNotesSection states', () => {
  it('shows loading and marks the region busy', () => {
    renderSection({ phase: 'loading', status: null });
    expect(screen.getByRole('status')).toHaveTextContent('Szukam powiązanych notatek…');
    expect(screen.getByRole('region', { name: 'Powiązane' })).toHaveAttribute('aria-busy', 'true');
  });

  it('shows a request error as an alert with a retry button', async () => {
    const onretry = vi.fn();
    renderSection({ phase: 'error', status: null, onretry });
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Nie udało się pobrać powiązanych notatek.',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Spróbuj ponownie' }));
    expect(onretry).toHaveBeenCalledOnce();
  });

  it.each([
    ['pending', 'Notatka jest jeszcze indeksowana'],
    ['unavailable', 'Wyszukiwanie semantyczne nie jest skonfigurowane.'],
    ['empty', 'Notatka nie ma jeszcze treści do porównania.'],
  ] as const)('shows the %s state', (status, text) => {
    renderSection({ status });
    expect(screen.getByRole('status')).toHaveTextContent(text);
  });

  it('distinguishes ready-with-no-results from the empty source', () => {
    renderSection({ status: 'ready', items: [] });
    expect(screen.getByRole('status')).toHaveTextContent(
      'Brak powiązanych notatek w tym zakresie.',
    );
    expect(screen.queryByText('Notatka nie ma jeszcze treści do porównania.')).toBeNull();
  });
});

describe('RelatedNotesSection results', () => {
  const items = [
    relatedItem({
      note_id: 'n-1',
      title: 'Alpha',
      folder: 'proj/a',
      target_header_path: ['Plan', 'Ryzyka'],
    }),
    relatedItem({ note_id: 'n-2', title: 'Beta', folder: '' }),
  ];

  it('links to the note in the tree with title, folder and heading', () => {
    renderSection({ items });
    const alpha = screen.getByRole('link', { name: /Alpha/ });
    expect(alpha).toHaveAttribute('href', '/workspace/ws/notes/proj/a/n-1');
    expect(alpha).toHaveTextContent('proj/a/');
    expect(alpha).toHaveTextContent('Plan › Ryzyka');
    expect(screen.getByRole('link', { name: /Beta/ })).toHaveAttribute(
      'href',
      '/workspace/ws/notes/n-2',
    );
  });

  it('keeps already-linked results, in order, and flags them', () => {
    renderSection({ items, relations: new Map([['n-2', 'backlink' as const]]) });
    const links = screen.getAllByRole('link');
    expect(links.map((l) => l.textContent)).toEqual([
      expect.stringContaining('Alpha'),
      expect.stringContaining('Beta'),
    ]);
    expect(within(links[1]).getByText(/Ta notatka linkuje do bieżącej/)).toBeInTheDocument();
    expect(within(links[0]).queryByText(/linkuj/)).toBeNull();
  });

  it('never renders ranking metrics', () => {
    const { container } = renderSection({ items });
    const text = container.textContent ?? '';
    for (const metric of ['0.4242', '0.1717', '0.3131', '0.8686', '%']) {
      expect(text).not.toContain(metric);
    }
  });
});

describe('RelatedNotesSection scope toggle', () => {
  it('reports the chosen scope and reflects the active one', async () => {
    const onscope = vi.fn();
    renderSection({ onscope });
    expect(screen.getByRole('button', { name: 'Workspace' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Folder' }));
    expect(onscope).toHaveBeenCalledWith('folder');
  });

  it('hides the toggle when the folder scope would be a no-op', () => {
    renderSection({ canScopeToFolder: false });
    expect(screen.queryByRole('group', { name: /Zakres/ })).toBeNull();
  });
});
