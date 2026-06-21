export type TreeNode = { name: string; fullPath: string; children: TreeNode[] };

export function buildTree(paths: string[]): TreeNode[] {
  const root: TreeNode[] = [];
  const map = new Map<string, TreeNode>();
  for (const path of [...paths].sort()) {
    const parts = path.split('/');
    const name = parts.at(-1)!;
    const node: TreeNode = { name, fullPath: path, children: [] };
    map.set(path, node);
    const parentPath = parts.slice(0, -1).join('/');
    if (parentPath && map.has(parentPath)) {
      map.get(parentPath)!.children.push(node);
    } else {
      root.push(node);
    }
  }
  return root;
}

/** All ancestor paths of a folder, including the folder itself. */
export function ancestors(folder: string): string[] {
  return folder ? folder.split('/').map((_, i, arr) => arr.slice(0, i + 1).join('/')) : [];
}

/** Immediate child folder paths of `parent` ('' = root), sorted. */
export function childFolders(folders: string[], parent: string): string[] {
  const prefix = parent ? `${parent}/` : '';
  const depth = parent ? parent.split('/').length : 0;
  return folders.filter((f) => f.startsWith(prefix) && f.split('/').length === depth + 1).sort();
}
