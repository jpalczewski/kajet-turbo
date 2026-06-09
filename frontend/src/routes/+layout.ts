import { apiSessionGetApiSessionGet } from '$lib/api'
import type { LayoutLoad } from './$types'

export const ssr = false

export const load: LayoutLoad = async () => {
  const result = await apiSessionGetApiSessionGet({ credentials: 'include' }).catch(() => null)
  return { session: result?.status === 200 ? (result.data as { email: string }) : null }
}
