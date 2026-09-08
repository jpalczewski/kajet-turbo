import { error, redirect } from '@sveltejs/kit';
import {
  apiGetNoteHtmlApiWorkspacesNameNotesNoteIdHtmlGet,
  apiListNotesApiWorkspacesNameNotesGet,
  apiListTagsApiWorkspacesNameTagsGet,
  apiNoteLinksApiWorkspacesNameNotesNoteIdLinksGet,
  apiWorkspaceContentsApiWorkspacesNameContentsGet,
} from '$lib/api';
import type {
  LinksResponse,
  NoteHtmlResponse,
  NoteItem,
  TagNode,
  WorkspaceContentsResponse,
} from '$lib/api';
import { loginPath, workspacesPath } from '$lib/routes';
import type { PageLoad } from './$types';

function statusOf(e: unknown): number | undefined {
  return (e as { status?: number } | null)?.status;
}

export const load: PageLoad = async ({ params, url, depends }) => {
  const slug = params.slug;
  depends('app:workspace-tree');

  const view = url.searchParams.get('view');
  if (view === 'tags') {
    const tagPath = params.path ? params.path.split('/').filter(Boolean).join('/') : '';
    const includeDescendants = url.searchParams.get('desc') !== '0';
    let tagsError: unknown = null;
    const [tagsResult, notesResult] = await Promise.all([
      apiListTagsApiWorkspacesNameTagsGet(slug).catch((e) => {
        tagsError = e;
        return null;
      }),
      tagPath
        ? apiListNotesApiWorkspacesNameNotesGet(slug, {
            tag: tagPath,
            include_descendants: includeDescendants,
          }).catch(() => null)
        : Promise.resolve(null),
    ]);
    if (statusOf(tagsError) === 401) redirect(307, loginPath());
    if (statusOf(tagsError) === 403) redirect(307, workspacesPath());
    // customFetch (fetcher.ts) throws on any non-2xx response, so a resolved result is
    // always the 200 variant at runtime -- narrow orval's per-status-code union to match.
    const tags: TagNode[] = tagsResult?.status === 200 ? tagsResult.data.tags : [];
    const notes: NoteItem[] = notesResult?.status === 200 ? notesResult.data.notes : [];
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

  let contents: WorkspaceContentsResponse;
  try {
    const result = await apiWorkspaceContentsApiWorkspacesNameContentsGet(
      slug,
      fullPath ? { path: fullPath } : undefined,
    );
    if (result.status !== 200) error(500, 'Błąd serwera.');
    contents = result.data;
  } catch (e) {
    const status = statusOf(e);
    if (status === 401) redirect(307, loginPath());
    if (status === 403) redirect(307, workspacesPath());
    if (status === 400) error(400, 'Nieprawidłowa ścieżka.');
    error(500, 'Błąd serwera.');
  }

  const folderPath = contents.folder_path;
  const noteId = contents.selected_note_id ?? contents.default_note_id;
  const noteSelected = contents.resolution === 'note';

  const [noteResult, linksResult] = noteId
    ? await Promise.all([
        apiGetNoteHtmlApiWorkspacesNameNotesNoteIdHtmlGet(slug, noteId).catch(() => null),
        apiNoteLinksApiWorkspacesNameNotesNoteIdLinksGet(slug, noteId).catch(() => null),
      ])
    : [null, null];

  const note: NoteHtmlResponse | null = noteResult?.status === 200 ? noteResult.data : null;
  const links: LinksResponse =
    linksResult?.status === 200 ? linksResult.data : { backlinks: [], outlinks: [] };

  return {
    mode: 'files' as const,
    notes: contents.notes,
    tree: { folders: contents.folders },
    folderPath,
    noteId,
    noteSelected,
    slug,
    note,
    links,
    // tag-mode fields kept so both branches share the same key set (clean union)
    tags: [] as TagNode[],
    tagPath: '',
    includeDescendants: true,
  };
};
