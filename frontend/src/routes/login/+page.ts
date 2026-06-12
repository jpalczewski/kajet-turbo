import { redirect } from '@sveltejs/kit';
import { apiPendingInfoApiPendingGet } from '$lib/api';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ url }) => {
  const pendingId = url.searchParams.get('pending') ?? '';

  if (!pendingId) redirect(307, '/');

  const result = await apiPendingInfoApiPendingGet({ id: pendingId }).catch(() => null);
  const clientName = result?.status === 200 ? result.data.client_name : 'Claude';

  return { pendingId, clientName };
};
