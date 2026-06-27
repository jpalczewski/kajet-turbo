export type ExplorerPane = 'list' | 'preview';

/**
 * Which explorer pane is the active one on a narrow (mobile) viewport.
 * `noteSelected` must come from `load` (the backend contents endpoint resolves
 * whether the current path is a folder or a note) — not from raw route params,
 * and not from `noteId` alone (a folder defaults its preview to its README,
 * which sets `noteId` without the user having selected a note).
 */
export function activePane(data: { noteSelected: boolean }): ExplorerPane {
  return data.noteSelected ? 'preview' : 'list';
}
