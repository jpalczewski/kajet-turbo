import type { LayoutLoad } from './$types'

export const ssr = false

export const load: LayoutLoad = async ({ fetch }) => {
  const res = await fetch('/api/session', { credentials: 'include' }).catch(() => null)
  return { session: res?.ok ? ((await res.json()) as { email: string }) : null }
}
