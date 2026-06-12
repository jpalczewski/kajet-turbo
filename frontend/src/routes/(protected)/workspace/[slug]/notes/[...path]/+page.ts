import { redirect } from '@sveltejs/kit';
import { loginPath, workspacesPath } from '$lib/routes';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, fetch, depends }) => {
  const slug = params.slug;
  const segments = params.path ? params.path.split('/').filter(Boolean) : [];
  const lastSegment = segments.at(-1) ?? null;
  const parentPath = segments.slice(0, -1).join('/');
  const fullPath = segments.join('/');

  // Fire all possible requests in a single round-trip.
  // We don't yet know if the last segment is a folder or a note, so we fetch
  // speculatively: notes for both fullPath (if folder) and parentPath (if note),
  // plus the note html (if it's a note). One of these will be discarded.
  depends('app:workspace-tree');
  const [lsResult, treeResult, notesInFolder, notesInParent, noteResult] = await Promise.all([
    fetch(`/api/workspaces/${slug}/ls?path=${fullPath}`, { credentials: 'include' }).catch(
      () => null,
    ),
    fetch(`/api/workspaces/${slug}/ls?recursive=true`, { credentials: 'include' }).catch(
      () => null,
    ),
    segments.length > 0
      ? fetch(`/api/workspaces/${slug}/notes?folder=${fullPath}`, { credentials: 'include' }).catch(
          () => null,
        )
      : Promise.resolve(null),
    fetch(`/api/workspaces/${slug}/notes?folder=${parentPath}`, { credentials: 'include' }).catch(
      () => null,
    ),
    lastSegment
      ? fetch(`/api/workspaces/${slug}/notes/${lastSegment}/html`, {
          credentials: 'include',
        }).catch(() => null)
      : Promise.resolve(null),
  ]);

  if (lsResult?.status === 401) redirect(307, loginPath());
  if (lsResult?.status === 403) redirect(307, workspacesPath());

  const isFolder = lsResult?.ok ?? true;
  const folderPath = isFolder ? fullPath : parentPath;
  const noteId = isFolder ? null : lastSegment;

  const notesResult = isFolder ? (notesInFolder ?? notesInParent) : notesInParent;
  const notes = notesResult?.ok ? (await notesResult.json()).notes : [];
  const tree = treeResult?.ok ? await treeResult.json() : { folders: [], entries: [] };
  const note = !isFolder && noteResult?.ok ? await noteResult.json() : null;

  return { notes, tree, folderPath, noteId, slug, note };
};
