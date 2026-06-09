import { redirect } from '@sveltejs/kit'

export const load = async ({ parent }: { parent: () => Promise<{ session: { email: string } | null }> }) => {
  const { session } = await parent()
  if (!session) redirect(307, '/login')
}
