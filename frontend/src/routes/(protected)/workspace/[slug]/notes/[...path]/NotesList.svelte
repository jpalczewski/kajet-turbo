<script lang="ts">
  import { goto } from '$app/navigation';
  import type { NoteItem } from '$lib/api';
  import EmptyState from '$lib/components/ui/EmptyState.svelte';
  import ListRow from '$lib/components/ui/ListRow.svelte';
  import { noteInTreePath } from '$lib/routes';
  import { formatDate, formatSize, type DateFormatPrefs } from '$lib/utils/format';
  import InlineCreateInput from './InlineCreateInput.svelte';

  let {
    notes,
    currentNoteId,
    folderPath,
    slug,
    onCreateNote,
    useNoteFolder = false,
    showCreate = true,
    datePrefs,
  }: {
    notes: NoteItem[];
    currentNoteId: string | null;
    folderPath: string;
    slug: string;
    onCreateNote: (title: string) => Promise<void>;
    useNoteFolder?: boolean;
    showCreate?: boolean;
    datePrefs: DateFormatPrefs;
  } = $props();

  function openNote(note: NoteItem) {
    goto(noteInTreePath(slug, useNoteFolder ? note.folder : folderPath, note.note_id));
  }

  let creating = $state(false);
</script>

<div class="notes-list">
  <div class="notes-list__header">
    <span class="notes-list__path">{folderPath || slug}/</span>
    <span class="notes-list__count">{notes.length}</span>
    {#if showCreate}
      <button class="create-btn" onclick={() => (creating = true)} title="Nowa notatka">+</button>
    {/if}
  </div>

  {#if creating}
    <div class="new-note-row">
      <InlineCreateInput
        variant="list"
        placeholder="tytuł-notatki"
        onsubmit={async (title) => {
          await onCreateNote(title);
          creating = false;
        }}
        oncancel={() => (creating = false)}
      />
    </div>
  {/if}

  {#if notes.length === 0}
    <EmptyState>Brak notatek.</EmptyState>
  {:else}
    <ul>
      {#each notes as note (note.note_id)}
        <li>
          <ListRow active={note.note_id === currentNoteId} onclick={() => openNote(note)}>
            <span class="note-title">{note.title}</span>
            <span class="note-meta">
              <span class="note-meta__item">{formatSize(note.size_bytes)}</span>
              <span class="note-meta__item">{formatDate(note.updated_at, datePrefs)}</span>
            </span>
          </ListRow>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .notes-list {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    border-left: 1px solid v.$border;
    border-right: 1px solid v.$border;

    &__header {
      display: flex;
      align-items: center;
      gap: v.$space-sm;
      padding: 8px 12px;
      border-bottom: 1px solid v.$border;
      flex-shrink: 0;
    }

    &__path {
      font-family: v.$font-mono;
      font-size: 0.72rem;
      color: v.$text-muted;
      letter-spacing: 0.03em;
    }

    &__count {
      font-family: v.$font-mono;
      font-size: 0.68rem;
      color: v.$accent-dark;
      background: rgba(240, 184, 0, 0.08);
      border: 1px solid v.$border;
      border-radius: v.$radius-sm;
      padding: 1px 6px;
    }

    ul {
      list-style: none;
      padding: 0;
      margin: 0;
      overflow-y: auto;
      flex: 1;
    }
  }

  .create-btn {
    margin-left: auto;
    background: none;
    border: none;
    color: v.$text-muted;
    font-family: v.$font-mono;
    font-size: 1rem;
    cursor: pointer;
    padding: 0 2px;
    line-height: 1;
    transition: color 0.15s;

    &:hover {
      color: v.$accent;
    }
  }

  .new-note-row {
    padding: 6px 12px;
    border-bottom: 1px solid v.$border;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .note-title {
    font-family: v.$font-mono;
    font-size: 0.85rem;
    color: v.$text-primary;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .note-meta {
    display: flex;
    gap: v.$space-sm;

    &__item {
      font-family: v.$font-mono;
      font-size: 0.68rem;
      color: v.$text-muted;
    }
  }
</style>
