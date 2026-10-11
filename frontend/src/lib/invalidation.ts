// The only place SvelteKit dependency keys are built. A `depends()` in a loader and
// the `invalidate()` that should rerun it are linked by nothing but string equality,
// so both sides go through these builders instead of spelling the key inline.

export const loadKeys = {
  session: 'app:session',
  workspaces: 'app:workspaces',
  // Lower-cased: the route slug and an event's canonical workspace name must meet even
  // where lookup tolerates a different case. Two names differing only in case then share
  // a key, which costs a spare refetch at worst.
  workspaceTree: (slug: string) => `app:workspace-tree:${slug.toLowerCase()}` as const,
  // Scoped like the tree key so an invalidation never reaches another workspace's
  // collection of the same name. Nothing invalidates these yet; loaders still declare them.
  collections: (slug: string) => `app:collections:${slug.toLowerCase()}` as const,
  collection: (slug: string, name: string) =>
    `app:collection:${slug.toLowerCase()}:${name}` as const,
  note: (noteId: string) => `app:note:${noteId}` as const,
} as const;
