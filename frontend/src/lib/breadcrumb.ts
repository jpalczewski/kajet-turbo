export type Crumb = { label: string; folder: string };

/** Folder split into cumulative breadcrumb crumbs; each `folder` is navigable. */
export function breadcrumbCrumbs(folder: string): Crumb[] {
  if (!folder) return [];
  const parts = folder.split('/');
  return parts.map((label, i) => ({ label, folder: parts.slice(0, i + 1).join('/') }));
}

/** Parent folder path of `folder` ('' at the root). */
export function parentFolder(folder: string): string {
  const i = folder.lastIndexOf('/');
  return i === -1 ? '' : folder.slice(0, i);
}
