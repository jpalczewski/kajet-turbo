import { resolve } from '$app/paths';

// The only place app URLs are built. Builders return resolve(...) directly so
// their inferred type is ResolvedPathname, which satisfies the
// svelte/no-navigation-without-resolve rule at every call site. Never
// concatenate onto a result outside this module - that widens to string.

const trimSlashes = (path: string) => path.replace(/^\/+|\/+$/g, '');

export const homePath = () => resolve('/');
export const loginPath = () => resolve('/login');
export const workspacesPath = () => resolve('/(protected)/workspaces');
export const settingsPath = () => resolve('/(protected)/settings');

export const notesPath = (slug: string, folder = '') =>
  resolve('/(protected)/workspace/[slug]/notes/[...path]', {
    slug,
    path: trimSlashes(folder),
  });

export const noteInTreePath = (slug: string, folder: string, noteId: string) =>
  notesPath(slug, folder ? `${trimSlashes(folder)}/${noteId}` : noteId);

export const notePath = (slug: string, id: string) =>
  resolve('/(protected)/workspace/[slug]/note/[id]', { slug, id });

export const noteEditPath = (slug: string, id: string) =>
  resolve('/(protected)/workspace/[slug]/note/[id]/edit', { slug, id });

export const noteHistoryPath = (slug: string, id: string) =>
  resolve('/(protected)/workspace/[slug]/note/[id]/history', { slug, id });
