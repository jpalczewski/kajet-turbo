import { redirect } from '@sveltejs/kit'
import type { PageLoad } from './$types'

export const load: PageLoad = async ({ url, fetch }) => {
  const pendingId = url.searchParams.get('pending') ?? ''

  if (!pendingId) redirect(307, '/')

  const res = await fetch(`/api/pending?id=${pendingId}`).catch(() => null)
  const clientName = res?.ok ? ((await res.json()).client_name ?? 'Claude') : 'Claude'

  return { pendingId, clientName }
}
