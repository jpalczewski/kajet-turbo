<script lang="ts">
  import { invalidate } from '$app/navigation';
  import { apiDeleteNoteApiWorkspacesNameNotesNoteIdDelete } from '$lib/api';
  import { apiErrorMessage } from '$lib/api/mutate';
  import ConfirmDialog from '$lib/components/ui/ConfirmDialog.svelte';
  import MoveNoteDialog from '$lib/components/MoveNoteDialog.svelte';
  import { noteChunksPath, noteEditPath, noteHistoryPath, notePath } from '$lib/routes';

  let {
    slug,
    noteId,
    folder,
    noteTitle,
    variant,
    onmoved,
    ondeleted,
  }: {
    slug: string;
    noteId: string;
    folder: string;
    noteTitle: string;
    variant: 'preview' | 'full';
    onmoved: (folder: string) => void | Promise<void>;
    ondeleted: () => void | Promise<void>;
  } = $props();

  async function deleteNote() {
    try {
      await apiDeleteNoteApiWorkspacesNameNotesNoteIdDelete(slug, noteId);
    } catch (e) {
      throw new Error(apiErrorMessage(e, 'Nie udało się usunąć notatki'));
    }
    await invalidate('app:workspace-tree');
    ondeleted();
  }
</script>

<div class="actions">
  {#if variant === 'full'}
    <a href={noteEditPath(slug, noteId)} class="actions__link">Edytuj</a>
  {/if}
  <a href={noteHistoryPath(slug, noteId)} class="actions__link">Historia</a>
  <a href={noteChunksPath(slug, noteId)} class="actions__link">Chunki</a>
  <MoveNoteDialog {slug} {noteId} currentFolder={folder} {onmoved} />
  <ConfirmDialog
    title="Usuń notatkę"
    message={`Usunąć "${noteTitle}"?`}
    confirmLabel="Usuń"
    confirmVariant="danger"
    onconfirm={deleteNote}
  >
    {#snippet trigger({ open })}
      <button class="actions__link actions__link--danger" onclick={open}>Usuń</button>
    {/snippet}
  </ConfirmDialog>
  {#if variant === 'preview'}
    <a
      href={notePath(slug, noteId)}
      class="actions__link actions__link--primary"
      title="Otwórz pełny widok">↗</a
    >
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .actions {
    display: flex;
    align-items: center;
    gap: v.$space-sm;
    flex-shrink: 0;
  }

  .actions__link {
    font-family: v.$font-mono;
    font-size: 0.72rem;
    color: v.$text-muted;
    text-decoration: none;
    white-space: nowrap;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    &:hover {
      color: v.$text-primary;
    }
    &--primary {
      color: v.$accent-dark;
      font-size: 0.8rem;
      &:hover {
        color: v.$accent;
      }
    }
    &--danger {
      &:hover {
        color: v.$error;
      }
    }
  }
</style>
