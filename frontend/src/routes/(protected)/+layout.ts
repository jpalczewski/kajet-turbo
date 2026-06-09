import { redirect } from '@sveltejs/kit'
import { apiListWorkspacesApiWorkspacesGet } from '$lib/api'
import type { LayoutLoad } from './$types'

export const load: LayoutLoad = async ({ parent }) => {
  const { session } = await parent()
  if (!session) redirect(307, '/login')
  const result = await apiListWorkspacesApiWorkspacesGet({ credentials: 'include' }).catch(() => null)
  const workspaces: string[] = result?.status === 200 ? ((result.data as any).workspaces as string[]) : []
  return { workspaces }
}
