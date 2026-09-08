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
  NotesListResponse,
  TagNode,
  TagsResponse,
  WorkspaceContentsResponse,
} from '$lib/api';
import { loginPath, workspacesPath } from '$lib/routes';
import type { PageLoad } from './$types';

function statusOf(e: unknown): number | undefined {
  return (e as { status?: number } | null)?.status;
}

// customFetch (fetcher.ts) throws on any non-2xx response, so a resolved result here is
// always the 200 variant at runtime -- this narrows orval's per-status-code response union
// down to that one. Not loadApi() (load.ts): that helper throws/redirects on a non-200
// result, but every call site below needs the opposite -- soft-fail to `null`/a caller
// -supplied default instead of erroring the whole page load.
function dataOr<T>(result: { status: number; data: unknown } | null | undefined): T | null {
  return result != null && result.status === 200 ? (result.data as T) : null;
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
    const tags: TagNode[] = dataOr<TagsResponse>(tagsResult)?.tags ?? [];
    const notes: NoteItem[] = dataOr<NotesListResponse>(notesResult)?.notes ?? [];
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

  const note = dataOr<NoteHtmlResponse>(noteResult);
  const links = dataOr<LinksResponse>(linksResult) ?? { backlinks: [], outlinks: [] };

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
