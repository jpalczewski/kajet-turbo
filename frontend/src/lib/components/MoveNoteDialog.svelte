<script lang="ts">
  import {
    apiLsApiWorkspacesNameLsGet,
    apiMoveNoteApiWorkspacesNameNotesNoteIdMovePost,
  } from '$lib/api';
  import { apiErrorMessage, jsonBody } from '$lib/api/mutate';
  import Modal from '$lib/components/ui/Modal.svelte';

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

  let modal: Modal;
  let folders = $state<string[]>([]);
  let destination = $state('');
  let loading = $state(false);
  let moving = $state(false);
  let error = $state('');

  async function openDialog() {
    loading = true;
    error = '';
    destination = '';
    modal.show();
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
      modal.close();
    } catch (e) {
      error = apiErrorMessage(e, 'Nie udało się przenieść notatki');
    } finally {
      moving = false;
    }
  }
</script>

<button class="move-trigger" onclick={openDialog}>Przenieś</button>

<Modal bind:this={modal} title="Przenieś notatkę">
  {#if loading}
    <p class="move-status">Ładowanie folderów…</p>
  {:else if folders.length === 0}
    <p class="move-status">Brak innych folderów docelowych.</p>
  {:else}
    <label class="move-field">
      Folder docelowy
      <select bind:value={destination}>
        {#each folders as folder (folder)}
          <option value={folder}>{folder || `${slug} (root)`}</option>
        {/each}
      </select>
    </label>
  {/if}

  {#if error}
    <p class="move-error">{error}</p>
  {/if}

  {#snippet actions()}
    <button class="btn btn--secondary" onclick={() => modal.close()} disabled={moving}
      >Anuluj</button
    >
    <button
      class="btn btn--primary"
      onclick={moveNote}
      disabled={loading || moving || folders.length === 0}
    >
      {moving ? 'Przenoszenie…' : 'Przenieś'}
    </button>
  {/snippet}
</Modal>

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

  .move-field {
    display: flex;
    flex-direction: column;
    gap: v.$space-sm;

    select {
      width: 100%;
      padding: 9px 12px;
      border: 1px solid v.$border;
      border-radius: v.$radius-md;
      background: v.$bg-surface;
      color: v.$text-primary;
      font-family: v.$font-mono;
    }
  }

  .move-status,
  .move-error {
    margin: 0;
    font-family: v.$font-mono;
    font-size: 0.8rem;
  }
  .move-status {
    color: v.$text-muted;
  }
  .move-error {
    color: v.$error;
  }
</style>
