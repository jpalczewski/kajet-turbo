import { apiSessionGetApiSessionGet } from '$lib/api'
import type { LayoutLoad } from './$types'

export const ssr = false

export const load: LayoutLoad = async () => {
  const result = await apiSessionGetApiSessionGet().catch(() => null)
  return { session: result?.data ?? null }
}
