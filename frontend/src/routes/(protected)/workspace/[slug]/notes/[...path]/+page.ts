import { error, redirect } from '@sveltejs/kit';
import type { NoteItem, TagNode } from '$lib/api';
import { loginPath, workspacesPath } from '$lib/routes';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, url, fetch, depends }) => {
  const slug = params.slug;
  depends('app:workspace-tree');

  const view = url.searchParams.get('view');
  if (view === 'tags') {
    const tagPath = params.path ? params.path.split('/').filter(Boolean).join('/') : '';
    const includeDescendants = url.searchParams.get('desc') !== '0';
    const [tagsResult, notesResult] = await Promise.all([
      fetch(`/api/workspaces/${slug}/tags`, { credentials: 'include' }).catch(() => null),
      tagPath
        ? fetch(
            `/api/workspaces/${slug}/notes?tag=${encodeURIComponent(tagPath)}` +
              `&include_descendants=${includeDescendants}`,
            { credentials: 'include' },
          ).catch(() => null)
        : Promise.resolve(null),
    ]);
    if (tagsResult?.status === 401) redirect(307, loginPath());
    if (tagsResult?.status === 403) redirect(307, workspacesPath());
    const tags: TagNode[] = tagsResult?.ok ? (await tagsResult.json()).tags : [];
    const notes: NoteItem[] = notesResult?.ok ? (await notesResult.json()).notes : [];
    return {
      mode: 'tags' as const,
      slug,
      tags,
      tagPath,
      includeDescendants,
      notes,
      noteSelected: false,
      // file-mode fields kept so the page's data shape stays consistent
      tree: { folders: [] },
      folderPath: '',
      noteId: null,
      note: null,
      links: { backlinks: [], outlinks: [] },
    };
  }

  const segments = params.path ? params.path.split('/').filter(Boolean) : [];
  const fullPath = segments.join('/');

  const contentsUrl = fullPath
    ? `/api/workspaces/${slug}/contents?path=${encodeURIComponent(fullPath)}`
    : `/api/workspaces/${slug}/contents`;
  const contentsResult = await fetch(contentsUrl, { credentials: 'include' }).catch(() => null);

  if (contentsResult?.status === 401) redirect(307, loginPath());
  if (contentsResult?.status === 403) redirect(307, workspacesPath());
  if (contentsResult?.status === 400) error(400, 'Nieprawidłowa ścieżka.');
  if (!contentsResult?.ok) error(500, 'Błąd serwera.');

  const contents = await contentsResult.json();
  const folderPath = contents.folder_path;
  const noteId = contents.selected_note_id ?? contents.default_note_id;
  const noteSelected = contents.resolution === 'note';

  const [noteResult, linksResult] = noteId
    ? await Promise.all([
        fetch(`/api/workspaces/${slug}/notes/${noteId}/html`, {
          credentials: 'include',
        }).catch(() => null),
        fetch(`/api/workspaces/${slug}/notes/${noteId}/links`, {
          credentials: 'include',
        }).catch(() => null),
      ])
    : [null, null];

  return {
    mode: 'files' as const,
    notes: contents.notes as NoteItem[],
    tree: { folders: contents.folders as string[] },
    folderPath,
    noteId,
    noteSelected,
    slug,
    note: noteResult?.ok ? await noteResult.json() : null,
    links: linksResult?.ok ? await linksResult.json() : { backlinks: [], outlinks: [] },
    // tag-mode fields kept so both branches share the same key set (clean union)
    tags: [] as TagNode[],
    tagPath: '',
    includeDescendants: true,
  };
};
