// The only place SvelteKit dependency keys are built. A `depends()` in a loader and
// the `invalidate()` that should rerun it are linked by nothing but string equality,
// so both sides go through these builders instead of spelling the key inline.

export const loadKeys = {
  session: 'app:session',
  workspaces: 'app:workspaces',
  workspaceTree: 'app:workspace-tree',
  collections: 'app:collections',
  collection: (name: string) => `app:collection:${name}` as const,
  note: (noteId: string) => `app:note:${noteId}` as const,
} as const;
