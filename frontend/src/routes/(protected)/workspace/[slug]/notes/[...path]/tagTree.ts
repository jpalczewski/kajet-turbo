import type { TagNode } from '$lib/api';

export type TagTreeNode = {
  name: string;
  fullPath: string;
  exactCount: number;
  descendantCount: number;
  children: TagTreeNode[];
};

/** Build a nested tag tree from the flat, count-annotated list returned by GET /tags. */
export function buildTagTree(tags: TagNode[]): TagTreeNode[] {
  const root: TagTreeNode[] = [];
  const map = new Map<string, TagTreeNode>();
  for (const tag of [...tags].sort((a, b) => a.path.localeCompare(b.path))) {
    const node: TagTreeNode = {
      name: tag.name,
      fullPath: tag.path,
      exactCount: tag.exact_count,
      descendantCount: tag.descendant_count,
      children: [],
    };
    map.set(tag.path, node);
    const parentPath = tag.path.split('/').slice(0, -1).join('/');
    if (parentPath && map.has(parentPath)) {
      map.get(parentPath)!.children.push(node);
    } else {
      root.push(node);
    }
  }
  return root;
}

/** All ancestor paths of a tag path, including itself (for auto-expand). */
export function tagAncestors(path: string): string[] {
  return path ? path.split('/').map((_, i, arr) => arr.slice(0, i + 1).join('/')) : [];
}
