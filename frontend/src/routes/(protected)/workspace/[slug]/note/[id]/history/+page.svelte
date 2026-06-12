<script lang="ts">
  import { page } from '$app/state';
  import { goto, invalidate } from '$app/navigation';
  import {
    apiNoteVersionApiWorkspacesNameNotesNoteIdHistoryShaGet,
    apiRestoreNoteVersionApiWorkspacesNameNotesNoteIdHistoryShaRestorePost,
  } from '$lib/api';
  import type { NoteHtmlResponse } from '$lib/api';
  import Prose from '$lib/components/Prose.svelte';
  import { notePath } from '$lib/routes';
  import VersionList from './VersionList.svelte';

  const slug = $derived(page.params.slug as string);
  const noteId = $derived(page.params.id as string);
  const entries = $derived(page.data.entries ?? []);

  let selectedSha = $state<string | null>(null);
  let selectedVersion = $state<NoteHtmlResponse | null>(null);
  let loading = $state(false);
  let restoring = $state(false);

  async function selectVersion(sha: string) {
    selectedSha = sha;
    loading = true;
    selectedVersion = null;
    const result = await apiNoteVersionApiWorkspacesNameNotesNoteIdHistoryShaGet(
      slug,
      noteId,
      sha,
    ).catch(() => null);
    loading = false;
    selectedVersion = result?.status === 200 ? result.data : null;
  }

  async function restore() {
    if (!selectedSha) return;
    restoring = true;
    await apiRestoreNoteVersionApiWorkspacesNameNotesNoteIdHistoryShaRestorePost(
      slug,
      noteId,
      selectedSha,
    ).catch(() => null);
    restoring = false;
    await invalidate('app:workspace-tree');
    goto(notePath(slug, noteId));
  }
</script>

<main class="page">
  <a href={notePath(slug, noteId)} class="back-link">← Wróć do notatki</a>
  <h1 class="page-title">Historia</h1>

  <div class="history-layout">
    <VersionList {entries} {selectedSha} onselect={selectVersion} />

    <section class="history-preview">
      {#if loading}
        <p class="history-preview__empty">Ładowanie...</p>
      {:else if selectedVersion}
        <div class="history-preview__actions">
          <button class="btn-restore" onclick={restore} disabled={restoring}>
            {restoring ? 'Przywracam...' : 'Przywróć tę wersję'}
          </button>
        </div>
        <Prose html={selectedVersion.content_html} />
      {:else}
        <p class="history-preview__empty">Wybierz wersję z listy po lewej.</p>
      {/if}
    </section>
  </div>
</main>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .page {
    max-width: 1100px;
    margin: 0 auto;
    padding: v.$space-2xl v.$space-lg;
  }

  .back-link {
    display: inline-block;
    font-size: 0.8rem;
    font-family: v.$font-mono;
    color: v.$text-secondary;
    text-decoration: none;
    margin-bottom: v.$space-lg;
    transition: color 0.15s;
    &:hover {
      color: v.$accent;
    }
  }

  .page-title {
    font-family: v.$font-mono;
    font-size: 1.25rem;
    color: v.$text-primary;
    margin: 0 0 v.$space-xl 0;
  }

  .history-layout {
    display: grid;
    grid-template-columns: 260px 1fr;
    gap: v.$space-xl;
    align-items: start;
  }

  .history-preview {
    min-height: 200px;

    &__empty {
      font-size: 0.85rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
    }

    &__actions {
      margin-bottom: v.$space-lg;
    }
  }

  .btn-restore {
    font-family: v.$font-mono;
    font-size: 0.8rem;
    padding: v.$space-sm v.$space-lg;
    border: 1px solid v.$accent-dark;
    border-radius: v.$radius-sm;
    background: none;
    color: v.$accent;
    cursor: pointer;
    transition:
      background 0.15s,
      color 0.15s;

    &:hover:not(:disabled) {
      background: v.$accent-dark;
      color: v.$bg-deep;
    }

    &:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  }
</style>
