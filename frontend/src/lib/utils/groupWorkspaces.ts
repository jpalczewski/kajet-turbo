import type { WorkspaceInfo } from '$lib/api';

export interface WorkspaceGroup {
  folder: string;
  items: WorkspaceInfo[];
}

export function groupWorkspaces(workspaces: WorkspaceInfo[]): WorkspaceGroup[] {
  const byFolder = new Map<string, WorkspaceInfo[]>();
  for (const ws of workspaces) {
    const key = ws.folder ?? '';
    (byFolder.get(key) ?? byFolder.set(key, []).get(key)!).push(ws);
  }
  return [...byFolder.entries()]
    .sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b)))
    .map(([folder, items]) => ({
      folder,
      items: items.sort((x, y) => x.name.localeCompare(y.name)),
    }));
}
