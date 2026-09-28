import { redirect } from '@sveltejs/kit';
import {
  apiGetNoteHtmlApiWorkspacesNameNotesNoteIdHtmlGet,
  apiListNotesApiWorkspacesNameNotesGet,
  apiListTagsApiWorkspacesNameTagsGet,
  apiNoteLinksApiWorkspacesNameNotesNoteIdLinksGet,
  apiWorkspaceContentsApiWorkspacesNameContentsGet,
} from '$lib/api';
import type { NoteItem, TagNode } from '$lib/api';
import { loadApi, loadApiOrNull } from '$lib/api/load';
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
        ? loadApiOrNull(
            apiListNotesApiWorkspacesNameNotesGet(slug, {
              tag: tagPath,
              include_descendants: includeDescendants,
            }),
          )
        : null,
    ]);
    if (statusOf(tagsError) === 401) redirect(307, loginPath());
    if (statusOf(tagsError) === 403) redirect(307, workspacesPath());
    const tags: TagNode[] = tagsResult?.data.tags ?? [];
    const notes: NoteItem[] = notesResult?.notes ?? [];
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

  const contents = (
    await loadApi(
      apiWorkspaceContentsApiWorkspacesNameContentsGet(
        slug,
        fullPath ? { path: fullPath } : undefined,
      ),
      'Nie znaleziono workspace’u.',
      {
        forbiddenRedirect: workspacesPath(),
        badRequest: 'Nieprawidłowa ścieżka.',
      },
    )
  ).data;

  const folderPath = contents.folder_path;
  const noteId = contents.selected_note_id ?? contents.default_note_id;
  const noteSelected = contents.resolution === 'note';

  const [note, linksData] = noteId
    ? await Promise.all([
        loadApiOrNull(apiGetNoteHtmlApiWorkspacesNameNotesNoteIdHtmlGet(slug, noteId)),
        loadApiOrNull(apiNoteLinksApiWorkspacesNameNotesNoteIdLinksGet(slug, noteId)),
      ])
    : [null, null];
  const links = linksData ?? { backlinks: [], outlinks: [] };

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
