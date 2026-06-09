import { redirect } from '@sveltejs/kit'
import type { NoteItem } from '$lib/types'
import type { PageLoad } from './$types'

export const load: PageLoad = async ({ params, fetch }) => {
  const res = await fetch(`/api/workspaces/${params.slug}/notes`, { credentials: 'include' }).catch(() => null)
  if (res?.status === 401) redirect(307, '/login')
  if (res?.status === 403) redirect(307, '/workspaces')
  const notes: NoteItem[] = res?.ok ? (await res.json()).notes : []
  return { notes }
}
