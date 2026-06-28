<script lang="ts">
  import {
    apiWorkspaceContentsApiWorkspacesNameContentsGet,
    apiMoveNoteApiWorkspacesNameNotesNoteIdMovePost,
  } from '$lib/api';
  import { jsonBody } from '$lib/api/mutate';
  import { useAsyncAction } from '$lib/utils/async-action.svelte';
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
  const fetchAction = useAsyncAction();
  const moveAction = useAsyncAction();

  async function openDialog() {
    moveAction.clearError();
    destination = '';
    modal.show();
    await fetchAction.run(async () => {
      const result = await apiWorkspaceContentsApiWorkspacesNameContentsGet(slug);
      if (result.status !== 200) throw new Error();
      folders = ['', ...(result.data.folders ?? [])].filter((folder) => folder !== currentFolder);
      destination = folders[0] ?? '';
    }, 'Nie udało się pobrać folderów');
  }

  async function moveNote() {
    if (fetchAction.busy || moveAction.busy || folders.length === 0) return;
    await moveAction.run(async () => {
      const result = await apiMoveNoteApiWorkspacesNameNotesNoteIdMovePost(
        slug,
        noteId,
        jsonBody({ folder: destination }),
      );
      if (result.status !== 200) throw new Error();
      await onmoved(result.data.folder);
      modal.close();
    }, 'Nie udało się przenieść notatki');
  }
</script>

<button class="move-trigger" onclick={openDialog}>Przenieś</button>

<Modal bind:this={modal} title="Przenieś notatkę">
  {#if fetchAction.busy}
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

  {#if fetchAction.error || moveAction.error}
    <p class="move-error">{fetchAction.error || moveAction.error}</p>
  {/if}

  {#snippet actions()}
    <button class="btn btn--secondary" onclick={() => modal.close()} disabled={moveAction.busy}
      >Anuluj</button
    >
    <button
      class="btn btn--primary"
      onclick={moveNote}
      disabled={fetchAction.busy || moveAction.busy || folders.length === 0}
    >
      {moveAction.busy ? 'Przenoszenie…' : 'Przenieś'}
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
