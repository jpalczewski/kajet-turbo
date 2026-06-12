<script lang="ts">
  import {
    apiLsApiWorkspacesNameLsGet,
    apiMoveNoteApiWorkspacesNameNotesNoteIdMovePost,
  } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';

  let {
    slug,
    noteId,
    currentFolder,
    onmoved,
  }: {
    slug: string;
    noteId: string;
    currentFolder: string;
    onmoved: (folder: string) => void | Promise<void>;
  } = $props();

  let dialog: HTMLDialogElement;
  let folders = $state<string[]>([]);
  let destination = $state('');
  let loading = $state(false);
  let moving = $state(false);
  let error = $state('');

  async function openDialog() {
    loading = true;
    error = '';
    destination = '';
    dialog.showModal();
    try {
      const result = await apiLsApiWorkspacesNameLsGet(slug, { recursive: true });
      if (result.status !== 200) throw new Error();
      folders = ['', ...(result.data.folders ?? [])].filter((folder) => folder !== currentFolder);
      destination = folders[0] ?? '';
    } catch (e) {
      error = apiErrorMessage(e, 'Nie udało się pobrać folderów');
    } finally {
      loading = false;
    }
  }

  async function moveNote() {
    if (loading || moving || folders.length === 0) return;
    moving = true;
    error = '';
    try {
      const result = await apiMoveNoteApiWorkspacesNameNotesNoteIdMovePost(
        slug,
        noteId,
        jsonBody({ folder: destination }),
      );
      if (result.status !== 200) throw new Error();
      await onmoved(result.data.folder);
      dialog.close();
    } catch (e) {
      error = apiErrorMessage(e, 'Nie udało się przenieść notatki');
    } finally {
      moving = false;
    }
  }
</script>

<button class="move-trigger" onclick={openDialog}>Przenieś</button>

<dialog
  bind:this={dialog}
  class="move-dialog"
  onclick={(e) => e.target === dialog && dialog.close()}
>
  <div class="move-dialog__content">
    <header class="move-dialog__header">
      <h2>Przenieś notatkę</h2>
      <button class="move-dialog__close" onclick={() => dialog.close()} title="Zamknij">×</button>
    </header>

    {#if loading}
      <p class="move-dialog__status">Ładowanie folderów…</p>
    {:else if folders.length === 0}
      <p class="move-dialog__status">Brak innych folderów docelowych.</p>
    {:else}
      <label>
        Folder docelowy
        <select bind:value={destination}>
          {#each folders as folder (folder)}
            <option value={folder}>{folder || `${slug} (root)`}</option>
          {/each}
        </select>
      </label>
    {/if}

    {#if error}
      <p class="move-dialog__error">{error}</p>
    {/if}

    <div class="move-dialog__actions">
      <button class="btn btn--secondary" onclick={() => dialog.close()} disabled={moving}>
        Anuluj
      </button>
      <button
        class="btn btn--primary"
        onclick={moveNote}
        disabled={loading || moving || folders.length === 0}
      >
        {moving ? 'Przenoszenie…' : 'Przenieś'}
      </button>
    </div>
  </div>
</dialog>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .move-trigger {
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

  .move-dialog {
    width: min(420px, calc(100vw - 32px));
    padding: 0;
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    background: v.$bg-raised;
    color: v.$text-primary;

    &::backdrop {
      background: rgba(0, 0, 0, 0.72);
    }

    &__content {
      display: flex;
      flex-direction: column;
      gap: v.$space-lg;
      padding: v.$space-lg;
    }

    &__header,
    &__actions {
      display: flex;
      align-items: center;
    }

    &__header {
      justify-content: space-between;
      gap: v.$space-md;

      h2 {
        margin: 0;
        font-family: v.$font-mono;
        font-size: 1rem;
      }
    }

    &__close {
      width: 28px;
      height: 28px;
      padding: 0;
      border: none;
      background: none;
      color: v.$text-muted;
      font-size: 1.25rem;
      cursor: pointer;

      &:hover {
        color: v.$text-primary;
      }
    }

    label {
      gap: v.$space-sm;
    }

    select {
      width: 100%;
      padding: 9px 12px;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      background: v.$bg-surface;
      color: v.$text-primary;
      font-family: v.$font-mono;
    }

    &__status,
    &__error {
      font-family: v.$font-mono;
      font-size: 0.8rem;
    }

    &__status {
      color: v.$text-muted;
    }

    &__error {
      color: v.$error;
    }

    &__actions {
      justify-content: flex-end;
      gap: v.$space-sm;
    }
  }
</style>
