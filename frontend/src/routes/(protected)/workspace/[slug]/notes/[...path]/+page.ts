import { redirect } from '@sveltejs/kit'
import type { PageLoad } from './$types'

export const load: PageLoad = async ({ params, fetch }) => {
  const slug = params.slug
  const segments = params.path ? params.path.split('/').filter(Boolean) : []

  const lsUrl = `/api/workspaces/${slug}/ls?path=${segments.join('/')}`
  const treeUrl = `/api/workspaces/${slug}/ls?recursive=true`

  const [lsResult, treeResult] = await Promise.all([
    fetch(lsUrl, { credentials: 'include' }).catch(() => null),
    fetch(treeUrl, { credentials: 'include' }).catch(() => null),
  ])

  if (lsResult?.status === 401) redirect(307, '/login')
  if (lsResult?.status === 403) redirect(307, '/workspaces')

  const isFolder = lsResult?.ok ?? true
  const folderPath = isFolder ? segments.join('/') : segments.slice(0, -1).join('/')
  const noteId = isFolder ? null : (segments.at(-1) ?? null)

  const notesUrl = `/api/workspaces/${slug}/notes?folder=${folderPath}`

  const [notesResult, noteResult] = await Promise.all([
    fetch(notesUrl, { credentials: 'include' }).catch(() => null),
    noteId
      ? fetch(`/api/workspaces/${slug}/notes/${noteId}/html`, { credentials: 'include' }).catch(() => null)
      : Promise.resolve(null),
  ])

  const notes = notesResult?.ok ? (await notesResult.json()).notes : []
  const tree = treeResult?.ok ? await treeResult.json() : { folders: [], entries: [] }
  const note = noteResult?.ok ? await noteResult.json() : null

  return { notes, tree, folderPath, noteId, slug, note }
}
