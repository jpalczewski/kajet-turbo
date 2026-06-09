import { redirect } from '@sveltejs/kit'
import type { LayoutLoad } from './$types'

export const load: LayoutLoad = async ({ parent, fetch }) => {
  const { session } = await parent()
  if (!session) redirect(307, '/login')
  const res = await fetch('/api/workspaces', { credentials: 'include' }).catch(() => null)
  const workspaces: string[] = res?.ok ? ((await res.json()).workspaces as string[]) : []
  return { workspaces }
}
