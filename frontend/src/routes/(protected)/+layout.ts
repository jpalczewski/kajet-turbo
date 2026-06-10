import { redirect } from '@sveltejs/kit'
import { apiListWorkspacesApiWorkspacesGet } from '$lib/api'
import type { WorkspaceInfo } from '$lib/api'
import type { LayoutLoad } from './$types'

export const load: LayoutLoad = async ({ parent }) => {
  const { session } = await parent()
  if (!session) redirect(307, '/login')
  const result = await apiListWorkspacesApiWorkspacesGet().catch(() => null)
  const workspaces: WorkspaceInfo[] = result?.data.workspaces ?? []
  return { workspaces }
}
