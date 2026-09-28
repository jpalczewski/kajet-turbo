import { redirect } from '@sveltejs/kit';
import { apiPendingInfoApiPendingGet } from '$lib/api';
import { loadApiOrNull } from '$lib/api/load';
import { homePath } from '$lib/routes';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ url }) => {
  const pendingId = url.searchParams.get('pending') ?? '';

  if (!pendingId) redirect(307, homePath());

  const pending = await loadApiOrNull(apiPendingInfoApiPendingGet({ id: pendingId }));
  const clientName = pending?.client_name ?? 'Claude';

  return { pendingId, clientName };
};
