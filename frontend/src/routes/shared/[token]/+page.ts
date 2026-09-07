import { apiGetPublicNoteApiPublicNotesTokenGet } from '$lib/api';
import type { NoteHtmlResponse } from '$lib/api';
import type { PageLoad } from './$types';

// No loadApi() here: that helper redirects a 401 to /login and throws SvelteKit's generic
// error() for a 404, which isn't what a chrome-less, no-auth public page wants. A missing,
// revoked, or malformed token all collapse to `note: null` -- rendered as one neutral
// "not found" state, matching the API's own non-leaky 404 (never-existed vs. revoked look
// identical on purpose). A non-404 failure (5xx, network) is a different, transient state
// though -- collapsing it into the same message would tell a visitor a live link doesn't
// exist just because the backend blipped, so it gets its own flag instead.
export const load: PageLoad = async ({ params }) => {
  try {
    const result = await apiGetPublicNoteApiPublicNotesTokenGet(params.token);
    const note: NoteHtmlResponse | null = result.status === 200 ? result.data : null;
    return { note, transientError: false };
  } catch (e) {
    const status = (e as { status?: number }).status;
    return { note: null as NoteHtmlResponse | null, transientError: status !== 404 };
  }
};
