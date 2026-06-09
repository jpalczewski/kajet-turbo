import { redirect } from '@sveltejs/kit'
import { apiListNotesApiWorkspacesNameNotesGet } from '$lib/api'
import type { NoteItem } from '$lib/types'
import type { PageLoad } from './$types'

export const load: PageLoad = async ({ params }) => {
  const result = await apiListNotesApiWorkspacesNameNotesGet(params.slug, { credentials: 'include' }).catch(() => null)
  const status = result?.status as number | undefined
  if (status === 401) redirect(307, '/login')
  if (status === 403) redirect(307, '/workspaces')
  const notes: NoteItem[] = status === 200 ? (result!.data as any).notes : []
  return { notes }
}
