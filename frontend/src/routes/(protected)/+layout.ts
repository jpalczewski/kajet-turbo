import { redirect } from '@sveltejs/kit';
import { apiListWorkspacesApiWorkspacesGet } from '$lib/api';
import type { WorkspaceInfo } from '$lib/api';
import { loadApiOrNull } from '$lib/api/load';
import { loginPath } from '$lib/routes';
import type { LayoutLoad } from './$types';

export const load: LayoutLoad = async ({ parent, depends }) => {
  depends('app:workspaces');
  const [{ session }, list] = await Promise.all([
    parent(),
    loadApiOrNull(apiListWorkspacesApiWorkspacesGet()),
  ]);
  if (!session) redirect(307, loginPath());
  const workspaces: WorkspaceInfo[] = list?.workspaces ?? [];
  return { workspaces };
};
