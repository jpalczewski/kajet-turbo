import type { NoteLinkItem } from '$lib/api';

export type RelatedScope = 'workspace' | 'folder';
export type LinkRelation = 'backlink' | 'outlink' | 'both';

// Related notes come from the current workspace only, so cross-workspace links can never
// mark a result. Both lists are the raw ones, not the xws-filtered view: hiding a link in
// the relations panel must not change whether a result counts as already linked.
export function linkRelations(
  slug: string,
  backlinks: NoteLinkItem[],
  outlinks: NoteLinkItem[],
): Map<string, LinkRelation> {
  const local = (link: NoteLinkItem) => !link.workspace || link.workspace === slug;
  const relations = new Map<string, LinkRelation>();
  for (const link of backlinks.filter(local)) relations.set(link.note_id, 'backlink');
  for (const link of outlinks.filter(local)) {
    relations.set(link.note_id, relations.has(link.note_id) ? 'both' : 'outlink');
  }
  return relations;
}

const HEADING_SEPARATOR = ' › ';
const HEADING_DEPTH = 2;

// The chunk's heading path can be arbitrarily deep; the panel is narrow, so keep the
// innermost levels. An empty path means the text sits before the first heading.
export function headingLabel(headerPath: string[]): string | null {
  const parts = headerPath.map((part) => part.trim()).filter(Boolean);
  return parts.length > 0 ? parts.slice(-HEADING_DEPTH).join(HEADING_SEPARATOR) : null;
}
