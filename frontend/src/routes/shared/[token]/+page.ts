import { apiGetPublicNoteApiPublicNotesTokenGet } from '$lib/api';
import type { NoteHtmlResponse } from '$lib/api';
import type { PageLoad } from './$types';

// No loadApi() here: that helper redirects a 401 to /login and throws SvelteKit's generic
// error() for a 404, which isn't what a chrome-less, no-auth public page wants. A missing,
// revoked, or malformed token all collapse to `note: null` -- rendered as one neutral
// "not found" state, matching the API's own non-leaky 404 (never-existed vs. revoked look
// identical on purpose).
export const load: PageLoad = async ({ params }) => {
  const note: NoteHtmlResponse | null = await apiGetPublicNoteApiPublicNotesTokenGet(params.token)
    .then((result) => (result.status === 200 ? result.data : null))
    .catch(() => null);
  return { note };
};
