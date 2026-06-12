<script lang="ts">
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import {
    apiNoteVersionApiWorkspacesNameNotesNoteIdHistoryShaGet,
    apiRestoreNoteVersionApiWorkspacesNameNotesNoteIdHistoryShaRestorePost,
  } from '$lib/api';

  const slug = $derived(page.params.slug as string);
  const noteId = $derived(page.params.id as string);
  const entries = $derived(page.data.entries ?? []);

  let selectedSha = $state<string | null>(null);
  let selectedVersion = $state<any>(null);
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
      { credentials: 'include' },
    ).catch(() => null);
    loading = false;
    selectedVersion = result?.data ?? null;
  }

  async function restore() {
    if (!selectedSha) return;
    restoring = true;
    await apiRestoreNoteVersionApiWorkspacesNameNotesNoteIdHistoryShaRestorePost(
      slug,
      noteId,
      selectedSha,
      { credentials: 'include' },
    ).catch(() => null);
    restoring = false;
    goto(`/workspace/${slug}/note/${noteId}`);
  }

  function formatDate(ts: number): string {
    return new Date(ts * 1000).toLocaleDateString('pl-PL', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
</script>

<main class="page">
  <a href="/workspace/{slug}/note/{noteId}" class="back-link">← Wróć do notatki</a>
  <h1 class="page-title">Historia</h1>

  <div class="history-layout">
    <aside class="history-list">
      {#if entries.length === 0}
        <p class="empty">Brak historii.</p>
      {/if}
      {#each entries as entry}
        <button
          class="history-entry"
          class:history-entry--active={selectedSha === entry.sha}
          onclick={() => selectVersion(entry.sha)}
        >
          <span class="history-entry__date">{formatDate(entry.timestamp)}</span>
          <span class="history-entry__msg">{entry.message}</span>
        </button>
      {/each}
    </aside>

    <section class="history-preview">
      {#if loading}
        <p class="history-preview__empty">Ładowanie...</p>
      {:else if selectedVersion}
        <div class="history-preview__actions">
          <button class="btn-restore" onclick={restore} disabled={restoring}>
            {restoring ? 'Przywracam...' : 'Przywróć tę wersję'}
          </button>
        </div>
        <div class="prose">
          {@html selectedVersion.content_html}
        </div>
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

  .history-list {
    border-right: 1px solid v.$border;
    padding-right: v.$space-lg;
    display: flex;
    flex-direction: column;
    gap: v.$space-xs;
  }

  .history-entry {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: v.$space-sm v.$space-md;
    border: 1px solid v.$border;
    border-radius: v.$radius-sm;
    background: none;
    cursor: pointer;
    text-align: left;
    transition:
      border-color 0.15s,
      background 0.15s;

    &:hover {
      border-color: v.$accent-dark;
    }

    &--active {
      border-color: v.$accent;
      background: v.$bg-raised;
    }

    &__date {
      font-size: 0.7rem;
      font-family: v.$font-mono;
      color: v.$text-muted;
    }

    &__msg {
      font-size: 0.8rem;
      font-family: v.$font-mono;
      color: v.$text-secondary;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
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

  .empty {
    font-size: 0.85rem;
    font-family: v.$font-mono;
    color: v.$text-muted;
  }

  .prose {
    color: v.$text-primary;
    font-size: 0.95rem;
    line-height: 1.7;

    :global(h1),
    :global(h2),
    :global(h3),
    :global(h4) {
      font-family: v.$font-mono;
      color: v.$text-primary;
      margin: v.$space-xl 0 v.$space-md 0;
      line-height: 1.3;
    }
    :global(h1) {
      font-size: 1.5rem;
    }
    :global(h2) {
      font-size: 1.25rem;
    }
    :global(h3) {
      font-size: 1.05rem;
    }
    :global(p) {
      margin: 0 0 v.$space-md 0;
    }
    :global(a) {
      color: v.$accent;
      text-decoration: underline;
      text-underline-offset: 3px;
    }
    :global(ul),
    :global(ol) {
      padding-left: v.$space-lg;
      margin: 0 0 v.$space-md 0;
    }
    :global(li) {
      margin-bottom: v.$space-xs;
    }
    :global(code) {
      font-family: v.$font-mono;
      font-size: 0.85em;
      background: v.$bg-raised;
      border: 1px solid v.$border;
      border-radius: v.$radius-sm;
      padding: 1px 5px;
      color: v.$accent-light;
    }
    :global(pre) {
      background: v.$bg-raised;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      padding: v.$space-md;
      overflow-x: auto;
      margin: 0 0 v.$space-md 0;
    }
    :global(pre code) {
      background: none;
      border: none;
      padding: 0;
      color: v.$text-primary;
      font-size: 0.85rem;
    }
    :global(blockquote) {
      margin: 0 0 v.$space-md 0;
      padding: v.$space-sm v.$space-md;
      border-left: 3px solid v.$accent-dark;
      background: v.$bg-raised;
      color: v.$text-secondary;
      font-style: italic;
    }
    :global(blockquote p) {
      margin: 0;
    }
  }
</style>
