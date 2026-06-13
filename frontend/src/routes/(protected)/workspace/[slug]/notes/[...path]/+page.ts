import { redirect } from '@sveltejs/kit';
import type { NoteItem, TagNode } from '$lib/api';
import { loginPath, workspacesPath } from '$lib/routes';
import type { PageLoad } from './$types';

// Ordering (README-first + natural) is done backend-side; here we only need to
// locate the README to default the folder preview to it.
const isReadme = (n: NoteItem) => n.title.trim().toLowerCase() === 'readme';

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
      // file-mode fields kept so the page's data shape stays consistent
      tree: { folders: [], entries: [] },
      folderPath: '',
      noteId: null,
      note: null,
      links: { backlinks: [], outlinks: [] },
    };
  }

  const segments = params.path ? params.path.split('/').filter(Boolean) : [];
  const lastSegment = segments.at(-1) ?? null;
  const parentPath = segments.slice(0, -1).join('/');
  const fullPath = segments.join('/');

  // Fire all possible requests in a single round-trip.
  // We don't yet know if the last segment is a folder or a note, so we fetch
  // speculatively: notes for both fullPath (if folder) and parentPath (if note),
  // plus the note html (if it's a note). One of these will be discarded.
  const [lsResult, treeResult, notesInFolder, notesInParent, noteResult, linksResult] =
    await Promise.all([
      fetch(`/api/workspaces/${slug}/ls?path=${fullPath}`, { credentials: 'include' }).catch(
        () => null,
      ),
      fetch(`/api/workspaces/${slug}/ls?recursive=true`, { credentials: 'include' }).catch(
        () => null,
      ),
      segments.length > 0
        ? fetch(`/api/workspaces/${slug}/notes?folder=${fullPath}`, {
            credentials: 'include',
          }).catch(() => null)
        : Promise.resolve(null),
      fetch(`/api/workspaces/${slug}/notes?folder=${parentPath}`, { credentials: 'include' }).catch(
        () => null,
      ),
      lastSegment
        ? fetch(`/api/workspaces/${slug}/notes/${lastSegment}/html`, {
            credentials: 'include',
          }).catch(() => null)
        : Promise.resolve(null),
      lastSegment
        ? fetch(`/api/workspaces/${slug}/notes/${lastSegment}/links`, {
            credentials: 'include',
          }).catch(() => null)
        : Promise.resolve(null),
    ]);

  if (lsResult?.status === 401) redirect(307, loginPath());
  if (lsResult?.status === 403) redirect(307, workspacesPath());

  const isFolder = lsResult?.ok ?? true;
  const folderPath = isFolder ? fullPath : parentPath;
  let noteId = isFolder ? null : lastSegment;

  const notesResult = isFolder ? (notesInFolder ?? notesInParent) : notesInParent;
  const notes: NoteItem[] = notesResult?.ok ? (await notesResult.json()).notes : [];
  const tree = treeResult?.ok ? await treeResult.json() : { folders: [], entries: [] };
  let note = !isFolder && noteResult?.ok ? await noteResult.json() : null;
  let links =
    !isFolder && linksResult?.ok ? await linksResult.json() : { backlinks: [], outlinks: [] };

  // Landing on a folder: default the preview to its README, if one exists.
  if (isFolder && !note) {
    const readme = notes.find(isReadme);
    if (readme) {
      const [readmeHtml, readmeLinks] = await Promise.all([
        fetch(`/api/workspaces/${slug}/notes/${readme.note_id}/html`, {
          credentials: 'include',
        }).catch(() => null),
        fetch(`/api/workspaces/${slug}/notes/${readme.note_id}/links`, {
          credentials: 'include',
        }).catch(() => null),
      ]);
      if (readmeHtml?.ok) {
        note = await readmeHtml.json();
        noteId = readme.note_id;
      }
      if (readmeLinks?.ok) links = await readmeLinks.json();
    }
  }

  return {
    mode: 'files' as const,
    notes,
    tree,
    folderPath,
    noteId,
    slug,
    note,
    links,
    // tag-mode fields kept so both branches share the same key set (clean union)
    tags: [] as TagNode[],
    tagPath: '',
    includeDescendants: true,
  };
};
