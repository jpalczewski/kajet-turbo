import { redirect } from '@sveltejs/kit';
import { apiListWorkspacesApiWorkspacesGet } from '$lib/api';
import type { WorkspaceInfo } from '$lib/api';
import { loginPath } from '$lib/routes';
import type { LayoutLoad } from './$types';

export const load: LayoutLoad = async ({ parent, depends }) => {
  depends('app:workspaces');
  const [{ session }, result] = await Promise.all([
    parent(),
    apiListWorkspacesApiWorkspacesGet().catch(() => null),
  ]);
  if (!session) redirect(307, loginPath());
  const workspaces: WorkspaceInfo[] = result?.status === 200 ? result.data.workspaces : [];
  return { workspaces };
};
