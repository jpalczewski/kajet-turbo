<script lang="ts">
  import MoveNoteDialog from '$lib/components/MoveNoteDialog.svelte';
  import { noteChunksPath, noteEditPath, noteHistoryPath, notePath } from '$lib/routes';

  let {
    slug,
    noteId,
    folder,
    variant,
    onmoved,
  }: {
    slug: string;
    noteId: string;
    folder: string;
    variant: 'preview' | 'full';
    onmoved: (folder: string) => void | Promise<void>;
  } = $props();
</script>

<div class="actions">
  {#if variant === 'full'}
    <a href={noteEditPath(slug, noteId)} class="actions__link">Edytuj</a>
  {/if}
  <a href={noteHistoryPath(slug, noteId)} class="actions__link">Historia</a>
  <a href={noteChunksPath(slug, noteId)} class="actions__link">Chunki</a>
  <MoveNoteDialog {slug} {noteId} currentFolder={folder} {onmoved} />
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
  }
</style>
