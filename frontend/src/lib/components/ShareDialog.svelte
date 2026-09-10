<script lang="ts">
  import {
    apiCreateShareLinkApiWorkspacesNameNotesNoteIdShareLinksPost,
    apiListShareLinksApiWorkspacesNameNotesNoteIdShareLinksGet,
    apiRevokeShareLinkApiWorkspacesNameNotesNoteIdShareLinksTokenDelete,
    apiUpdateShareLinkPreviewApiWorkspacesNameNotesNoteIdShareLinksTokenPatch,
  } from '$lib/api';
  import type { ShareLinkItem } from '$lib/api';
  import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';
  import Modal from '$lib/components/ui/Modal.svelte';
  import { sharedNoteUrl } from '$lib/routes';
  import { copyToClipboard } from '$lib/utils/clipboard';
  import { useAsyncAction } from '$lib/utils/async-action.svelte';
  import { formatDate, formatDateTime, type DateFormatPrefs } from '$lib/utils/format';

  let { slug, noteId, datePrefs }: { slug: string; noteId: string; datePrefs: DateFormatPrefs } =
    $props();

  let modal: Modal;
  let links = $state<ShareLinkItem[]>([]);
  let copiedToken = $state('');
  let copyErrorToken = $state('');
  let previewToggleErrorToken = $state('');
  let createPreviewDescription = $state(false);
  let copyResetTimeout: ReturnType<typeof setTimeout> | undefined;
  const fetchAction = useAsyncAction();
  const createAction = useAsyncAction();

  function linkUrl(token: string): string {
    return sharedNoteUrl(window.location.origin, token);
  }

  async function openDialog() {
    // Guard against a double-click re-entering while the first call's dialog.show()
    // and fetch are still in flight: a second showModal() on an already-open <dialog>
    // throws, and a second GET can race the first and clobber `links`.
    if (fetchAction.busy) return;
    modal.show();
    await fetchAction.run(async () => {
      const result = await apiListShareLinksApiWorkspacesNameNotesNoteIdShareLinksGet(slug, noteId);
      if (result.status !== 200) throw new Error();
      links = result.data.links;
    }, 'Nie udało się pobrać linków');
  }

  async function createLink() {
    await createAction.run(async () => {
      const result = await apiCreateShareLinkApiWorkspacesNameNotesNoteIdShareLinksPost(
        slug,
        noteId,
        { preview_description: createPreviewDescription },
      );
      if (result.status !== 201) throw new Error();
      links = [...links, result.data];
    }, 'Nie udało się utworzyć linku');
  }

  async function revokeLink(token: string) {
    const result = await apiRevokeShareLinkApiWorkspacesNameNotesNoteIdShareLinksTokenDelete(
      slug,
      noteId,
      token,
    );
    if (result.status !== 200) throw new Error('Nie udało się wyłączyć linku');
    links = links.filter((link) => link.token !== token);
  }

  async function togglePreviewDescription(token: string, value: boolean) {
    previewToggleErrorToken = '';
    // Optimistic: flip the checkbox immediately, revert it if the PATCH fails.
    links = links.map((link) =>
      link.token === token ? { ...link, preview_description: value } : link,
    );
    try {
      // customFetch throws on a non-2xx response (see $lib/api/fetcher.ts) instead of
      // resolving with a non-200 status -- a plain status check here would never fire.
      await apiUpdateShareLinkPreviewApiWorkspacesNameNotesNoteIdShareLinksTokenPatch(
        slug,
        noteId,
        token,
        { preview_description: value },
      );
    } catch {
      links = links.map((link) =>
        link.token === token ? { ...link, preview_description: !value } : link,
      );
      previewToggleErrorToken = token;
    }
  }

  async function copyLink(token: string) {
    copyErrorToken = '';
    const ok = await copyToClipboard(linkUrl(token));
    if (!ok) {
      // Clipboard access can be denied (permissions, insecure context, browser
      // policy) -- surface it instead of leaving the click looking like a no-op;
      // the read-only URL field above stays there as a manual-copy fallback.
      copyErrorToken = token;
      return;
    }
    clearTimeout(copyResetTimeout);
    copiedToken = token;
    copyResetTimeout = setTimeout(() => {
      // Only this call's own token clears the indicator -- otherwise copying a
      // second link within the window would wipe the second link's confirmation
      // early when the first link's timer fires.
      if (copiedToken === token) copiedToken = '';
    }, 1500);
  }

  function resetDialogState() {
    fetchAction.clearError();
    createAction.clearError();
    copyErrorToken = '';
    previewToggleErrorToken = '';
  }
</script>

<button class="share-trigger" onclick={openDialog}>Udostępnij</button>

<Modal bind:this={modal} title="Linki do udostępniania" onclose={resetDialogState}>
  <label class="share-create-option">
    <input type="checkbox" bind:checked={createPreviewDescription} />
    Dołącz fragment treści w podglądzie linku
  </label>
  <button
    class="btn btn--primary"
    onclick={createLink}
    disabled={createAction.busy || fetchAction.busy}
  >
    {createAction.busy ? 'Tworzenie…' : 'Utwórz nowy link'}
  </button>
  {#if createAction.error}
    <p class="share-error">{createAction.error}</p>
  {/if}

  {#if fetchAction.busy}
    <p class="share-status">Ładowanie…</p>
  {:else if fetchAction.error}
    <p class="share-error">{fetchAction.error}</p>
  {:else if links.length === 0}
    <p class="share-status">Brak aktywnych linków dla tej notatki.</p>
  {:else}
    <ul class="share-list">
      {#each links as link (link.token)}
        <li class="share-list__item">
          <div class="share-list__info">
            <input
              class="share-list__url"
              type="text"
              readonly
              value={linkUrl(link.token)}
              onclick={(e) => e.currentTarget.select()}
            />
            <span class="share-list__date">Utworzono: {formatDate(link.created_at, datePrefs)}</span
            >
            <span class="share-list__date">Otwarcia strony: {link.page_view_count}</span>
            <span class="share-list__date">
              Ostatnie otwarcie:
              {link.last_page_viewed_at
                ? formatDateTime(link.last_page_viewed_at, datePrefs)
                : 'brak'}
            </span>
            <span class="share-list__date">Pobrania treści: {link.visit_count}</span>
            <span class="share-list__date">
              Ostatnie pobranie:
              {link.last_visited_at ? formatDateTime(link.last_visited_at, datePrefs) : 'brak'}
            </span>
            <label class="share-list__option">
              <input
                type="checkbox"
                checked={link.preview_description}
                onchange={(e) => togglePreviewDescription(link.token, e.currentTarget.checked)}
              />
              Fragment treści w podglądzie
            </label>
            {#if copyErrorToken === link.token}
              <span class="share-error">
                Nie udało się skopiować automatycznie — zaznacz link powyżej i skopiuj (Ctrl/Cmd+C).
              </span>
            {/if}
            {#if previewToggleErrorToken === link.token}
              <span class="share-error">Nie udało się zapisać zmiany.</span>
            {/if}
          </div>
          <div class="share-list__actions">
            <button class="btn btn--secondary" onclick={() => copyLink(link.token)}>
              {copiedToken === link.token ? 'Skopiowano!' : 'Kopiuj'}
            </button>
            <ConfirmDialog
              title="Wyłącz link"
              message="Ten link przestanie działać natychmiast."
              confirmLabel="Wyłącz"
              confirmVariant="danger"
              onconfirm={() => revokeLink(link.token)}
            >
              {#snippet trigger({ open })}
                <button class="btn btn--danger" onclick={open}>Wyłącz</button>
              {/snippet}
            </ConfirmDialog>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</Modal>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .share-trigger {
    padding: 0;
    border: none;
    background: none;
    color: v.$accent-dark;
    font-family: v.$font-mono;
    font-size: 0.72rem;
    cursor: pointer;

    &:hover {
      color: v.$accent;
    }
  }

  .share-create-option,
  .share-list__option {
    display: flex;
    align-items: center;
    gap: v.$space-xs;
    font-family: v.$font-mono;
    font-size: 0.75rem;
    color: v.$text-secondary;
    cursor: pointer;
  }

  .share-create-option {
    margin-bottom: v.$space-sm;
  }

  .share-status,
  .share-error {
    margin: 0;
    font-family: v.$font-mono;
    font-size: 0.8rem;
  }
  .share-status {
    color: v.$text-muted;
  }
  .share-error {
    color: v.$error;
  }

  .share-list {
    display: flex;
    flex-direction: column;
    gap: v.$space-sm;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .share-list__item {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: v.$space-sm;
    padding: v.$space-sm;
    border: 1px solid v.$border;
    border-radius: v.$radius-md;
  }

  .share-list__info {
    display: flex;
    flex-direction: column;
    gap: v.$space-xs;
    flex: 1;
    min-width: 0;
  }

  .share-list__url {
    width: 100%;
    padding: v.$space-xs v.$space-sm;
    border: 1px solid v.$border;
    border-radius: v.$radius-sm;
    background: v.$bg-surface;
    color: v.$text-secondary;
    font-family: v.$font-mono;
    font-size: 0.72rem;
    cursor: text;
  }

  .share-list__date {
    font-family: v.$font-mono;
    font-size: 0.75rem;
    color: v.$text-muted;
  }

  .share-list__actions {
    display: flex;
    gap: v.$space-xs;
    flex-shrink: 0;
  }
</style>
